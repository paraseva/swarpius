"""Tests for prompt caching support (cache_control markers, section
ordering, cross-provider gating).
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import yaml

from app.coordinator.context_providers import TextContextProvider
from app.coordinator.request_flow import (
    _apply_tool_cache_control,
    _build_system_message,
    _format_system_message,
    _supports_cache_markers,
)
from app.llm.client import _extract_usage
from app.llm.tool_loop import _accumulate_usage
from app.runtime.state import RuntimeState
from usage_metrics import UsageTracker


class TestExtractUsageWithCacheFields(unittest.TestCase):
    """_extract_usage should include cache-specific token counts when present."""

    def test_cache_fields_extracted(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=200,
                total_tokens=1200,
                cache_creation_input_tokens=500,
                cache_read_input_tokens=300,
            )
        )
        usage = _extract_usage(response)
        assert usage["cache_creation_input_tokens"] == 500
        assert usage["cache_read_input_tokens"] == 300

    def test_openai_nested_cached_tokens_extracted(self):
        """OpenAI surfaces cache hits via usage.prompt_tokens_details.cached_tokens.
        LiteLLM doesn't remap this to Anthropic's top-level field name — we
        must read the nested shape ourselves.
        """
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=2006,
                completion_tokens=300,
                total_tokens=2306,
                prompt_tokens_details=SimpleNamespace(cached_tokens=1920),
            )
        )
        usage = _extract_usage(response)
        assert usage["cache_read_input_tokens"] == 1920
        # OpenAI doesn't distinguish cache writes; creation stays 0
        assert usage["cache_creation_input_tokens"] == 0

    def test_anthropic_top_level_takes_priority_over_nested(self):
        """If both fields are present (e.g. an Anthropic response LiteLLM
        decorated with nested details), the Anthropic top-level is
        authoritative — they're identical for Anthropic in practice.
        """
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=200,
                total_tokens=1200,
                cache_read_input_tokens=800,
                prompt_tokens_details=SimpleNamespace(cached_tokens=800),
            )
        )
        usage = _extract_usage(response)
        assert usage["cache_read_input_tokens"] == 800

    def test_no_usage_object(self):
        response = SimpleNamespace()
        usage = _extract_usage(response)
        assert usage == {}


class TestFormatSystemMessage(unittest.TestCase):
    """System message formatting with and without prompt caching."""

    def test_caching_disabled_returns_plain_string(self):
        msg = _format_system_message("Hello world", cache_enabled=False)
        assert msg == {"role": "system", "content": "Hello world"}

class TestFormatSystemMessageSplit(unittest.TestCase):
    """When caching is enabled and the caller provides a split prefix +
    tail, _format_system_message must emit two content blocks with
    cache_control on BOTH — the inner marker caches the static prefix
    across requests, the outer marker caches the full prompt within a
    request's tool loop.
    """

    def test_split_enabled_cache_both_blocks_have_marker(self):
        """Both blocks get cache_control — static for cross-request,
        tail for intra-request tool loop.
        """
        msg = _format_system_message(
            "STATIC", cache_enabled=True, dynamic_tail="DYNAMIC",
        )
        content = msg["content"]
        assert content[0]["text"] == "STATIC"
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert content[1]["text"] == "DYNAMIC"
        assert content[1]["cache_control"] == {"type": "ephemeral"}

    def test_empty_dynamic_tail_falls_back_to_single_block(self):
        """If the dynamic tail is empty there's nothing to mark separately;
        behave like the original single-block format.
        """
        msg = _format_system_message(
            "STATIC", cache_enabled=True, dynamic_tail="",
        )
        content = msg["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["text"] == "STATIC"
        assert content[0]["cache_control"] == {"type": "ephemeral"}

class TestApplyToolCacheControl(unittest.TestCase):
    """cache_control marker on the last tool definition."""

    def test_adds_cache_control_to_last_tool(self):
        tools = [
            {"type": "function", "function": {"name": "tool_a", "parameters": {}}},
            {"type": "function", "function": {"name": "tool_b", "parameters": {}}},
        ]
        _apply_tool_cache_control(tools)
        # Only the last tool should have cache_control
        assert "cache_control" not in tools[0]["function"]
        assert tools[1]["function"]["cache_control"] == {"type": "ephemeral"}

    def test_single_tool(self):
        tools = [
            {"type": "function", "function": {"name": "tool_a", "parameters": {}}},
        ]
        _apply_tool_cache_control(tools)
        assert tools[0]["function"]["cache_control"] == {"type": "ephemeral"}

    def test_empty_list_is_noop(self):
        tools = []
        _apply_tool_cache_control(tools)  # should not raise
        assert tools == []


class TestAccumulateUsageCacheFields(unittest.TestCase):
    """_accumulate_usage should sum cache fields across steps."""

    def test_cache_fields_accumulated(self):
        total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        _accumulate_usage(total, {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "cache_creation_input_tokens": 80, "cache_read_input_tokens": 0,
        })
        _accumulate_usage(total, {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 60,
        })
        assert total["cache_creation_input_tokens"] == 80
        assert total["cache_read_input_tokens"] == 60
        assert total["input_tokens"] == 200



class TestUsageTrackerCacheFields(unittest.TestCase):
    """UsageTracker.record() should track and emit cache token fields."""

    def test_cache_fields_in_session_totals(self):
        tracker = UsageTracker()
        tracker.record(
            agent_name="Coordinator",
            input_tokens=1000, output_tokens=200, total_tokens=1200,
            cache_creation_input_tokens=500, cache_read_input_tokens=0,
            source="provider",
        )
        payload = tracker.record(
            agent_name="Coordinator",
            input_tokens=1000, output_tokens=200, total_tokens=1200,
            cache_creation_input_tokens=0, cache_read_input_tokens=400,
            source="provider",
        )
        assert payload["session_totals"]["cache_creation_input_tokens"] == 500
        assert payload["session_totals"]["cache_read_input_tokens"] == 400



class TestContextSectionOrdering(unittest.TestCase):
    """get_context_sections() must return sections in graded-staleness
    order so the static prefix (base + Skills + Zone Aliases + Current
    Date) can be cached separately from dynamic content.

    Order (most static → most dynamic):
      Skill Definitions → Zone Aliases → Current Date → Current Time
      → Zone Status → Execution Trace → Search History
      → Conversation History → Key Rules (last for recency attention)
    """

    EXPECTED_ORDER = [
        "Skill Definitions",
        "Zone Aliases",
        "Current Date",
        "Current Time",
        "Zone Status",
        "Execution Trace",
        "Search History",
        "Conversation History",
        "Key Rules",
    ]

    def _make_populated_runtime(self) -> RuntimeState:
        """Construct a RuntimeState with every provider returning non-empty
        content so get_context_sections() includes all of them.
        """
        runtime = RuntimeState()
        # TextContextProvider-backed sections — set directly
        runtime.skills_provider.set_context("<skills>")
        runtime.execution_trace_provider.set_context("<trace>")
        runtime.search_history_provider.set_context("<search>")
        runtime.key_rules_provider.set_context("<rules>")
        # Conversation history needs a real turn to report content
        runtime.conversation_history_provider.add_turn("hi", "hello")
        # Callback-backed sections — replace with text providers carrying
        # the same title so get_info() returns non-empty deterministically
        runtime.zone_aliases_provider = TextContextProvider("Zone Aliases")
        runtime.zone_aliases_provider.set_context("<aliases>")
        runtime.zone_status_provider = TextContextProvider("Zone Status")
        runtime.zone_status_provider.set_context("<status>")
        # CurrentDate/CurrentTime providers are always non-empty by design
        return runtime

    def test_all_sections_present_in_expected_order(self):
        runtime = self._make_populated_runtime()
        titles = [section["title"] for section in runtime.get_context_sections()]
        self.assertEqual(titles, self.EXPECTED_ORDER)



class TestBuildSystemMessageSplit(unittest.TestCase):
    """_build_system_message must return (static_prefix, dynamic_tail) so
    the caller can place a cache_control marker at the boundary.

    Static prefix = coordinator_system_prompt + Skills + Zone Aliases +
    Current Date. Dynamic tail = Current Time onward (including Key
    Rules at the end).
    """

    def _make_populated_runtime(self) -> RuntimeState:
        runtime = RuntimeState()
        runtime.coordinator_system_prompt = "BASE_PROMPT"
        runtime.skills_provider.set_context("<skills>")
        runtime.execution_trace_provider.set_context("<trace>")
        runtime.search_history_provider.set_context("<search>")
        runtime.key_rules_provider.set_context("<rules>")
        runtime.conversation_history_provider.add_turn("hi", "hello")
        runtime.zone_aliases_provider = TextContextProvider("Zone Aliases")
        runtime.zone_aliases_provider.set_context("<aliases>")
        runtime.zone_status_provider = TextContextProvider("Zone Status")
        runtime.zone_status_provider.set_context("<status>")
        # set_prompt_state_context() would overwrite search_history_provider
        # with the contents of runtime.search_history (empty here). Neutralise
        # it so our direct set_context("<search>") marker survives.
        runtime.set_prompt_state_context = lambda: None
        return runtime

    def test_static_prefix_contains_base_and_static_sections(self):
        runtime = self._make_populated_runtime()
        static_prefix, _ = _build_system_message(runtime)
        self.assertIn("BASE_PROMPT", static_prefix)
        self.assertIn("Skill Definitions", static_prefix)
        self.assertIn("<skills>", static_prefix)
        self.assertIn("Zone Aliases", static_prefix)
        self.assertIn("<aliases>", static_prefix)
        self.assertIn("Current Date", static_prefix)

    def test_dynamic_tail_contains_dynamic_sections(self):
        runtime = self._make_populated_runtime()
        _, dynamic_tail = _build_system_message(runtime)
        self.assertIn("Current Time", dynamic_tail)
        self.assertIn("Zone Status", dynamic_tail)
        self.assertIn("<status>", dynamic_tail)
        self.assertIn("Execution Trace", dynamic_tail)
        self.assertIn("<trace>", dynamic_tail)
        self.assertIn("Search History", dynamic_tail)
        self.assertIn("<search>", dynamic_tail)
        self.assertIn("Conversation History", dynamic_tail)
        self.assertIn("Key Rules", dynamic_tail)
        self.assertIn("<rules>", dynamic_tail)

    def test_concatenation_produces_full_original_prompt(self):
        """static_prefix + dynamic_tail must equal the full assembled
        system prompt (no content lost at the boundary).
        """
        runtime = self._make_populated_runtime()
        static_prefix, dynamic_tail = _build_system_message(runtime)
        combined = static_prefix + dynamic_tail
        # Every piece of content must be present exactly once
        self.assertEqual(combined.count("BASE_PROMPT"), 1)
        self.assertEqual(combined.count("<skills>"), 1)
        self.assertEqual(combined.count("<aliases>"), 1)
        self.assertEqual(combined.count("<trace>"), 1)
        self.assertEqual(combined.count("<rules>"), 1)

class TestSupportsCacheMarkers(unittest.TestCase):
    """_supports_cache_markers gates cache_control emission to providers
    that actually accept inline markers. Anthropic and Gemini do (LiteLLM
    auto-translates cache_control for Gemini to cachedContents).
    OpenAI and DeepSeek cache automatically and don't accept markers.
    """

    def test_anthropic_models_support_markers(self):
        self.assertTrue(_supports_cache_markers("anthropic/claude-sonnet-4-6"))
        self.assertTrue(_supports_cache_markers("anthropic/claude-opus-4-7"))
        self.assertTrue(_supports_cache_markers("anthropic/claude-haiku-4-5"))

    def test_gemini_models_support_markers(self):
        self.assertTrue(_supports_cache_markers("gemini/gemini-2.5-pro"))
        self.assertTrue(_supports_cache_markers("vertex_ai/gemini-2.5-pro"))

    def test_openai_models_do_not_support_markers(self):
        """OpenAI caches automatically; markers are neither required nor
        accepted. Don't send them.
        """
        self.assertFalse(_supports_cache_markers("openai/gpt-5"))
        self.assertFalse(_supports_cache_markers("openai/gpt-5.4"))
        self.assertFalse(_supports_cache_markers("openai/gpt-4o"))

    def test_deepseek_does_not_support_markers(self):
        """DeepSeek works the same as OpenAI — automatic caching only."""
        self.assertFalse(_supports_cache_markers("deepseek/deepseek-chat"))

    def test_local_providers_do_not_support_markers(self):
        self.assertFalse(_supports_cache_markers("ollama_chat/gemma4:26b"))
        self.assertFalse(_supports_cache_markers("ollama/llama3"))

    def test_empty_or_missing_model_returns_false(self):
        self.assertFalse(_supports_cache_markers(""))
        self.assertFalse(_supports_cache_markers(None))


class TestUsageTrackerCostField(unittest.TestCase):
    """UsageTracker.record() must accept a per-call cost_usd and surface
    it in both the call payload and session totals so the frontend can
    show running USD cost alongside token counts.

    Cost is computed by the LLM client via litellm.completion_cost(),
    which accounts for cross-provider cache read/write pricing.
    """

    def test_cost_usd_accumulates_in_session_totals(self):
        tracker = UsageTracker()
        tracker.record(
            agent_name="Coordinator",
            input_tokens=1000, output_tokens=200, total_tokens=1200,
            cost_usd=0.01,
            source="provider",
        )
        payload = tracker.record(
            agent_name="Coordinator",
            input_tokens=500, output_tokens=100, total_tokens=600,
            cost_usd=0.005,
            source="provider",
        )
        self.assertAlmostEqual(
            payload["session_totals"]["cost_usd"], 0.015, places=6,
        )

    def test_none_cost_treated_as_zero(self):
        """LiteLLM completion_cost can return None for unknown models;
        the tracker must not crash or produce a NaN total.
        """
        tracker = UsageTracker()
        payload = tracker.record(
            agent_name="Coordinator",
            input_tokens=1000, output_tokens=200, total_tokens=1200,
            cost_usd=None,
            source="provider",
        )
        self.assertEqual(payload["call"]["cost_usd"], 0.0)
        self.assertEqual(payload["session_totals"]["cost_usd"], 0.0)


class TestLLMResponseCostField(unittest.TestCase):
    """_extract_usage (or the LLM client wrapping it) must populate
    cost_usd on the usage dict so it flows through the tool loop's
    _accumulate_usage into the tracker.
    """

    def test_accumulate_usage_sums_cost(self):
        total: Dict[str, Any] = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0,
        }
        _accumulate_usage(total, {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "cost_usd": 0.005,
        })
        _accumulate_usage(total, {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "cost_usd": 0.003,
        })
        self.assertAlmostEqual(total["cost_usd"], 0.008, places=6)

class TestCostPersistenceInLogs(unittest.TestCase):
    """Verify cost_usd survives the round-trip through log_coordinator_step
    (step YAML) and log_outcome (outcome.json) so the analysis view can
    render historical costs.
    """

    def _make_logger(self, tmpdir: Path):
        from app.runtime.request_logger import RequestLogger
        return RequestLogger(request_id="rq-c01-0001", logs_root=tmpdir)

    def test_step_yaml_includes_cost_usd(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = self._make_logger(Path(tmp))
            logger.log_coordinator_step(
                step=1,
                coordinator_input=[{"role": "user", "content": "hi"}],
                coordinator_output={"action": "text_response", "text": "hello"},
                duration_ms=500,
                usage={
                    "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "cost_usd": 0.00123,
                },
            )
            step_files = list(Path(tmp).rglob("step_01.yaml"))
            self.assertEqual(len(step_files), 1)
            data = yaml.safe_load(step_files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["usage"]["cost_usd"], 0.00123)

    def test_outcome_json_includes_aggregated_cost(self):
        """log_outcome stores the accumulated usage dict verbatim —
        including the summed cost_usd, not a pre-rounded version.
        """
        with tempfile.TemporaryDirectory() as tmp:
            logger = self._make_logger(Path(tmp))
            # Aggregated usage represents cost summed across steps
            accumulated = {
                "input_tokens": 500, "output_tokens": 100, "total_tokens": 600,
                "cache_read_input_tokens": 300, "cache_creation_input_tokens": 0,
                "cost_usd": 0.01234,
            }
            logger.log_outcome(
                status="completed", chat_response="done", total_steps=3,
                coordinator_model="anthropic/claude-sonnet-4-6",
                usage=accumulated,
            )
            outcome_files = list(Path(tmp).rglob("outcome.json"))
            self.assertEqual(len(outcome_files), 1)
            data = json.loads(outcome_files[0].read_text(encoding="utf-8"))
            # Full precision preserved — rounding happens at render time
            self.assertEqual(data["usage"]["cost_usd"], 0.01234)

