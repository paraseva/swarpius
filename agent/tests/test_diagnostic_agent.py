"""Tests for DiagnosticAgent: LLM-driven conversation classification."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.llm.diagnostic_agent import (
    ConversationAssignment,
    DiagnosticAgent,
    truncate_response,
)
from app.runtime.conversation_tracker import ConversationTracker

# Flips ENABLE_DIAGNOSTIC_AGENT on for a test class. is_diagnostic_agent_enabled
# reads env at call time, so this takes effect regardless of what's in .env.
# Without it, assign_conversation short-circuits to None before calling the
# LLM client and the assertions below have nothing to inspect.
_enabled_env = patch.dict("os.environ", {"ENABLE_DIAGNOSTIC_AGENT": "true"})
_disabled_env = patch.dict("os.environ", {"ENABLE_DIAGNOSTIC_AGENT": "false"})


class MockClock:
    def __init__(self, start: float = 1000.0):
        self._time = start

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


def _make_tracker(**kwargs):
    clock = MockClock()
    kwargs.setdefault("idle_timeout_seconds", 300)
    tracker = ConversationTracker(clock=clock, **kwargs)
    return tracker, clock


def _make_mock_client(response_text: str):
    """Create a mock LLMClient that returns the given text."""
    from app.llm.client import LLMResponse

    client = AsyncMock()
    client.completion.return_value = LLMResponse(text=response_text)
    return client


@_enabled_env
class TestPromptBuilding(unittest.TestCase):
    """Verify the diagnostic agent builds correct prompts."""

    def test_prompt_includes_active_threads(self):
        tracker, clock = _make_tracker()
        tracker.assign_by_timeout()
        tracker.update_topic("c01", "Playing jazz music")
        clock.advance(10)
        client = _make_mock_client('{"conversation_id": "c01", "is_new": false, "topic_summary": "Playing jazz music"}')
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)

        asyncio.run(agent.assign_conversation("play more jazz"))

        call_args = client.completion.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        assert "c01" in user_msg
        assert "Playing jazz music" in user_msg


class TestTruncateResponse(unittest.TestCase):
    """Verify truncate_response produces compact summaries."""

    def test_short_text_passes_through(self):
        assert truncate_response("Playing jazz now.") == "Playing jazz now."

    def test_long_text_truncated_with_ellipsis(self):
        long_text = "word " * 50  # 250 chars
        result = truncate_response(long_text)
        assert len(result) <= 125
        assert result.endswith("…")

    def test_html_tags_stripped(self):
        text = "Here is <extended_info><summary>info</summary>a long list</extended_info> the result."
        result = truncate_response(text)
        assert "<" not in result
        assert ">" not in result

    def test_first_sentence_extracted(self):
        text = "Found 3 versions of Criticize. Here are the details in a long listing that goes on and on."
        result = truncate_response(text)
        assert result == "Found 3 versions of Criticize."

    def test_markdown_bold_stripped(self):
        text = "**Now playing** *All Around the World* by Alexander O'Neal"
        result = truncate_response(text)
        assert "**" not in result
        assert "Now playing" in result

    def test_empty_string(self):
        assert truncate_response("") == ""

    def test_whitespace_collapsed(self):
        text = "Found   three\n\nversions."
        result = truncate_response(text)
        assert result == "Found three versions."


@_enabled_env
class TestPromptRecencyAndContext(unittest.TestCase):
    """Verify the prompt includes recency, last response, and most-recent marker."""

    def test_prompt_includes_recency(self):
        tracker, clock = _make_tracker()
        tracker.assign_by_timeout()  # c01
        tracker.update_topic("c01", "Jazz session")
        clock.advance(120)  # 2 minutes
        client = _make_mock_client('{"conversation_id": "c01", "is_new": false, "topic_summary": "Jazz"}')
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        asyncio.run(agent.assign_conversation("more jazz"))
        user_msg = client.completion.call_args[1]["messages"][1]["content"]
        assert "2m ago" in user_msg

    def test_prompt_includes_last_response(self):
        tracker, clock = _make_tracker()
        tracker.assign_by_timeout()  # c01
        tracker.update_topic("c01", "Jazz session")
        tracker.set_last_response("c01", "Now playing Take Five by Dave Brubeck")
        clock.advance(10)
        client = _make_mock_client('{"conversation_id": "c01", "is_new": false, "topic_summary": "Jazz"}')
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        asyncio.run(agent.assign_conversation("thanks"))
        user_msg = client.completion.call_args[1]["messages"][1]["content"]
        assert "Now playing Take Five" in user_msg

    def test_prompt_omits_last_response_when_empty(self):
        tracker, clock = _make_tracker()
        tracker.assign_by_timeout()  # c01
        tracker.update_topic("c01", "Jazz session")
        clock.advance(10)
        client = _make_mock_client('{"conversation_id": "c01", "is_new": false, "topic_summary": "Jazz"}')
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        asyncio.run(agent.assign_conversation("test"))
        user_msg = client.completion.call_args[1]["messages"][1]["content"]
        assert "Last response" not in user_msg

    def test_prompt_marks_most_recent(self):
        tracker, clock = _make_tracker(idle_timeout_seconds=10)
        tracker.assign_by_timeout()  # c01
        tracker.update_topic("c01", "First topic")
        clock.advance(11)
        tracker.assign_by_timeout()  # c02
        tracker.update_topic("c02", "Second topic")
        clock.advance(5)
        client = _make_mock_client('{"conversation_id": "c02", "is_new": false, "topic_summary": "Second"}')
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        asyncio.run(agent.assign_conversation("test"))
        user_msg = client.completion.call_args[1]["messages"][1]["content"]
        # c02 (most recent) should be marked, c01 should not
        lines = user_msg.split("\n")
        c02_line = [ln for ln in lines if "c02" in ln][0]
        c01_line = [ln for ln in lines if "c01" in ln][0]
        assert "most recent" in c02_line.lower()
        assert "most recent" not in c01_line.lower()


@_enabled_env
class TestResponseParsing(unittest.TestCase):
    """Verify the agent correctly parses LLM responses."""

    def test_parse_existing_conversation(self):
        tracker, _ = _make_tracker()
        tracker.assign_by_timeout()
        client = _make_mock_client(
            '{"conversation_id": "c01", "is_new": false, "topic_summary": "Playing jazz"}'
        )
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        result = asyncio.run(agent.assign_conversation("more jazz"))

        assert result is not None
        assert result.conversation_id == "c01"
        assert result.is_new is False
        assert result.topic_summary == "Playing jazz"

    def test_parse_markdown_code_block(self):
        tracker, _ = _make_tracker()
        client = _make_mock_client(
            '```json\n{"conversation_id": "c01", "is_new": true, "topic_summary": "test"}\n```'
        )
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        result = asyncio.run(agent.assign_conversation("test"))

        assert result is not None
        assert result.conversation_id == "c01"

    def test_parse_invalid_json_returns_none(self):
        tracker, _ = _make_tracker()
        client = _make_mock_client("This is not JSON at all")
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        result = asyncio.run(agent.assign_conversation("test"))

        assert result is None

    def test_parse_new_conversation_without_conversation_id(self):
        """LLM returns is_new: true without conversation_id — should parse OK."""
        tracker, _ = _make_tracker()
        tracker.assign_by_timeout()  # c01 exists
        client = _make_mock_client(
            '{"is_new": true, "topic_summary": "General knowledge question"}'
        )
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        result = asyncio.run(agent.assign_conversation("what's today's date"))

        assert result is not None
        assert result.is_new is True
        assert result.topic_summary == "General knowledge question"

    def test_parse_missing_fields_returns_none(self):
        tracker, _ = _make_tracker()
        client = _make_mock_client('{"some_other_field": "value"}')
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        result = asyncio.run(agent.assign_conversation("test"))

        assert result is None

    def test_parse_picks_last_json_object_when_model_reasons_first(self):
        """The docstring on _parse_response says it extracts the LAST
        JSON object because models sometimes reason before producing
        the decision. Pre-fix the regex used re.search, which returns
        the FIRST match — so a response that quoted an earlier decision
        before producing the real one would mis-assign the conversation.
        """
        tracker, _ = _make_tracker()
        tracker.assign_by_timeout()  # mint c01
        reasoning_then_decision = (
            'Previous assignment was {"conversation_id": "c01", "is_new": false} '
            'but for this new user input the right decision is '
            '{"conversation_id": "c02", "is_new": true, '
            '"topic_summary": "New topic"}'
        )
        client = _make_mock_client(reasoning_then_decision)
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)
        result = asyncio.run(agent.assign_conversation("different topic"))

        assert result is not None
        assert result.conversation_id == "c02", (
            f"Expected the last JSON object to win; got {result.conversation_id!r} "
            "(the first-match bug would pick c01 from the reasoning text)"
        )
        assert result.is_new is True
        assert result.topic_summary == "New topic"


@_enabled_env
class TestFallbackBehaviour(unittest.TestCase):
    """Verify graceful fallback on errors."""

    def test_llm_error_propagates_to_caller(self):
        """LLM exceptions must surface to ``_run_diagnostic_classification``
        so the failure reason can be carried on ``call_failed``. The
        outer helper handles the fallback to timeout-based assignment;
        this layer should not swallow the exception."""
        tracker, _ = _make_tracker()
        client = AsyncMock()
        client.completion.side_effect = Exception("API error")
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)

        with self.assertRaises(Exception) as ctx:
            asyncio.run(agent.assign_conversation("test"))
        assert "API error" in str(ctx.exception)

    @_disabled_env
    def test_disabled_returns_none(self):
        tracker, _ = _make_tracker()
        client = _make_mock_client("should not be called")
        agent = DiagnosticAgent(llm_client=client, tracker=tracker)

        result = asyncio.run(agent.assign_conversation("test"))
        assert result is None
        client.completion.assert_not_called()


@_enabled_env
class TestTrackerIntegration(unittest.TestCase):
    """Verify apply_assignment updates the tracker correctly."""

    def test_apply_new_conversation_updates_assignment_id(self):
        """When is_new=True, apply_assignment should write the minted ID back to the assignment."""
        tracker, _ = _make_tracker()
        tracker.assign_by_timeout()  # c01
        agent = DiagnosticAgent(llm_client=AsyncMock(), tracker=tracker)

        assignment = ConversationAssignment(
            conversation_id="__new__", is_new=True, topic_summary="Fresh topic"
        )
        agent.apply_assignment(assignment)

        # The minted ID (c02) should be written back to the assignment object
        assert assignment.conversation_id == "c02"
        assert tracker.current_id == "c02"

    def test_apply_existing_conversation(self):
        tracker, clock = _make_tracker(idle_timeout_seconds=10)
        tracker.assign_by_timeout()  # c01
        tracker.update_topic("c01", "Jazz session")
        clock.advance(11)
        tracker.assign_by_timeout()  # c02 (timeout)
        agent = DiagnosticAgent(llm_client=AsyncMock(), tracker=tracker)

        assignment = ConversationAssignment(
            conversation_id="c01", is_new=False, topic_summary="Jazz session continued"
        )
        agent.apply_assignment(assignment)

        assert tracker.current_id == "c01"
        c01 = [t for t in tracker.get_active_threads() if t.id == "c01"][0]
        assert c01.topic_summary == "Jazz session continued"

    def test_apply_updates_topic_on_current(self):
        tracker, _ = _make_tracker()
        tracker.assign_by_timeout()  # c01
        agent = DiagnosticAgent(llm_client=AsyncMock(), tracker=tracker)

        assignment = ConversationAssignment(
            conversation_id="c01", is_new=False, topic_summary="Refined topic"
        )
        agent.apply_assignment(assignment)

        assert tracker.current_id == "c01"
        c01 = tracker.get_active_threads()[0]
        assert c01.topic_summary == "Refined topic"


@_disabled_env
class TestRequestFlowWithDiagnosticDisabled(unittest.TestCase):
    """End-to-end check that ENABLE_DIAGNOSTIC_AGENT=false propagates
    cleanly through process_request: no classification call is made,
    and the request_complete payload omits topic_summary (a
    diagnostic-agent artefact) but still carries conversation_id
    derived from the request_id, so the frontend can group the
    request into its cNN bucket."""

    def test_request_complete_omits_topic_summary_when_disabled(self):
        import os
        from unittest.mock import MagicMock

        from app.constants import CHANNEL_AGENT_OUTPUTS
        from app.coordinator.request_flow import process_request
        from app.llm.client import LLMResponse
        from app.runtime.state import RuntimeState

        try:
            from tests._runtime_fixtures import WSCapture
        except ModuleNotFoundError:
            from _runtime_fixtures import WSCapture  # type: ignore[no-redef]

        env = {
            "DEFAULT_ROON_ZONE": "Living Room",
            "SEARXNG_URL": "http://localhost:8081",
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "dummy-key",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("app.runtime.state.RoonConnection", MagicMock),
            patch("app.runtime.state._load_agent_skills", return_value=[]),
            patch(
                "app.runtime.state._format_agent_skills_for_prompt",
                return_value=("", ""),
            ),
        ):
            runtime = RuntimeState()
            runtime.ensure_initialised()

        async def _fake_completion(messages, tools=None):
            return LLMResponse(
                text="ok",
                tool_calls=[],
                usage={
                    "input_tokens": 10, "output_tokens": 1, "total_tokens": 11,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0, "cost_usd": 0.0,
                },
            )

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _fake_completion
        # diagnostic_client should NOT be called when feature flag is off,
        # but the runtime still has one wired. Replace it with a mock
        # whose completion would fail loudly if invoked.
        runtime.diagnostic_client = MagicMock()
        runtime.diagnostic_client.completion = AsyncMock(
            side_effect=AssertionError(
                "diagnostic_client.completion called even though "
                "ENABLE_DIAGNOSTIC_AGENT=false",
            ),
        )

        try:
            from tests._runtime_fixtures import wire_ws_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_ws_test_bus  # type: ignore[no-redef]
        capture = WSCapture()
        process_request(
            runtime=runtime,
            user_input="play jazz",
            cancel_event=None,
            event_bus=wire_ws_test_bus(capture, runtime),
            run_mode_label="ws",
        )

        agent_events = capture.payloads_on(CHANNEL_AGENT_OUTPUTS)
        completes = [e for e in agent_events if e.get("event_type") == "request_complete"]
        self.assertEqual(len(completes), 1)
        complete = completes[0]
        self.assertNotIn("topic_summary", complete)
        # conversation_id must still be present — derived from the
        # request_id ("rq-cNN-NNNN") so the frontend can group requests
        # into the right cNN bucket without the diagnostic agent.
        self.assertIn("conversation_id", complete)
        request_id = complete["request_id"]
        expected_cnn = request_id.split("-")[1]
        self.assertEqual(complete["conversation_id"], expected_cnn)
        # Sanity: the assignment side-channel for the analyser must
        # also be silent. Diagnostic agent emits 'diagnostic_active' on
        # AGENT_OUTPUTS when running; absence is the contract.
        self.assertFalse(
            any(e.get("event_type") == "diagnostic_active" for e in agent_events),
            "Diagnostic active marker leaked despite feature flag off",
        )
