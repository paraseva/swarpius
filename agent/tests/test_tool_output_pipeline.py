"""Integration tests for the tool output pipeline — the registry's
``compact_output`` and ``get_result_entries`` hooks that format tool
results into the conversation.

Scope: LLM calls tool → executor returns (stubbed) output schema →
tool_instance's compact_output / get_result_entries runs → tool result
message enters conversation, result handle minted. The fake executors
return hand-built output-schema instances, but every schema is
constructed through the real Pydantic model so shape changes (new
required fields, type changes) fail at import time.

What this does NOT exercise: the tools' own ``run_async`` logic
(browse-session orchestration, Roon-API calls, reference resolution).
That surface is covered by test_roon_browse_integration.py (live) and
test_complex_shuffle.py / test_reference_resolution.py (offline fakes).

These tests run through ``run_tool_loop`` with a fake LLM client and
real tool_instances so the registry's formatting hooks fire for real.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from app.llm.client import LLMResponse, ToolCall
from app.llm.tool_loop import run_tool_loop
from app.llm.tool_registry import ToolRegistry
from roon_core.schemas import RoonCoreItemSummarySchema, RoonCoreResultsGroupSchema
from tools.roon_search import (
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
    RoonSearchToolOutputSchema,
)
from tools.roon_status import RoonStatusToolOutputSchema
from tools.web_search import (
    SearXNGSearchTool,
    SearXNGSearchToolConfig,
    WebSearchResultItemSchema,
    WebSearchToolInputSchema,
    WebSearchToolOutputSchema,
)

# ── Fake LLM client ──────────────────────────────────────────────

class _FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def completion(self, messages, tools=None, temperature=0.0):
        idx = min(self._idx, len(self._responses) - 1)
        self._idx += 1
        return self._responses[idx]


def _text(text):
    return LLMResponse(text=text, tool_calls=[], usage={
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
    })


def _tool_call(name, args, call_id="call_1"):
    return LLMResponse(text=None, tool_calls=[
        ToolCall(id=call_id, name=name, arguments=args),
    ], usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


# ── Fake tool executors ──────────────────────────────────────────

_SEARCH_OUTPUT = RoonSearchToolOutputSchema(
    description="Search results for 'Kate Bush'.",
    groups=[
        RoonCoreResultsGroupSchema(
            group="-",
            items=[
                RoonCoreItemSummarySchema(
                    title="Kate Bush", reference="5661c",
                    extra_info="9 Albums", group="-",
                ),
                RoonCoreItemSummarySchema(
                    title="Albums", reference="96ba0",
                    extra_info="26 Results", group="-",
                ),
            ],
        ),
    ],
)


async def _fake_search(params):
    return _SEARCH_OUTPUT


_SEARXNG_OUTPUT = WebSearchToolOutputSchema(
    results=[
        WebSearchResultItemSchema(
            title="Kate Bush discography",
            url="https://en.wikipedia.org/wiki/Kate_Bush_discography",
            content="Overview of Kate Bush albums.",
            query="kate bush discography",
        ),
    ],
    category="general",
)


async def _fake_searxng(params):
    return _SEARXNG_OUTPUT


_STATUS_OUTPUT = RoonStatusToolOutputSchema(
    operation="get_zones_status",
    result="Playing: Hounds of Love by Kate Bush",
)


async def _fake_status(params):
    return _STATUS_OUTPUT


# ── Helpers ──────────────────────────────────────────────────────

def _make_registry():
    reg = ToolRegistry()
    search_tool = RoonSearchTool(RoonSearchToolConfig())
    reg.register("roon_search", "Search Roon library",
                 RoonSearchToolInputSchema, _fake_search,
                 tool_instance=search_tool)
    searxng_tool = SearXNGSearchTool(SearXNGSearchToolConfig())
    reg.register("web_search", "Web search",
                 WebSearchToolInputSchema, _fake_searxng,
                 tool_instance=searxng_tool)
    from tools.roon_status import RoonStatusToolInputSchema
    reg.register("roon_status", "Zone status",
                 RoonStatusToolInputSchema, _fake_status)
    return reg


def _get_tool_messages(messages):
    return [m for m in messages if m.get("role") == "tool"]


# ── Tests ────────────────────────────────────────────────────────

class TestRoonSearchPipeline(unittest.TestCase):
    """roon_search output flows through registry.compact_output into the conversation."""

    def test_roon_search_compacted_in_conversation(self):
        """Tool result message contains the one-line-per-item compact format,
        not raw JSON."""
        client = _FakeLLMClient([
            _tool_call("roon_search", {
                "operation": "new_search", "search_string": "Kate Bush",
            }),
            _text("Found Kate Bush."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "find kate bush"}]

        asyncio.run(run_tool_loop(
            client, registry, messages,

        ))

        tool_msgs = _get_tool_messages(messages)
        self.assertEqual(len(tool_msgs), 1)
        content = tool_msgs[0]["content"]

        # Must be the compact format, not JSON
        self.assertNotIn("{", content)
        lines = content.split("\n")
        self.assertEqual(lines[0], "Search results for 'Kate Bush'. 2 results.")
        self.assertEqual(lines[1], "(1) [5661c] Kate Bush | 9 Albums")
        self.assertEqual(lines[2], "(2) [96ba0] Albums | 26 Results")

    def test_handles_inline_via_on_store_results(self):
        """on_store_results returns handles that appear inline in the compact output."""
        client = _FakeLLMClient([
            _tool_call("roon_search", {
                "operation": "new_search", "search_string": "Kate Bush",
            }),
            _text("Found Kate Bush."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "find kate bush"}]

        def _fake_store(entries):
            return ["res_00001"]

        asyncio.run(run_tool_loop(
            client, registry, messages,
            on_store_results=_fake_store,
        ))

        tool_msgs = _get_tool_messages(messages)
        content = tool_msgs[0]["content"]
        self.assertTrue(content.startswith("[Result handle: res_00001]\n"))
        # Compact format follows the handle
        self.assertIn("(1) [5661c] Kate Bush | 9 Albums", content)


class TestSearXNGPipeline(unittest.TestCase):
    """web_search output (SearXNG backend) flows through registry.compact_output with aligned fields."""

    def test_searxng_formatted_in_conversation(self):
        """Tool result message contains aligned field format, not raw JSON."""
        client = _FakeLLMClient([
            _tool_call("web_search", {
                "queries": ["kate bush discography"], "category": "general",
            }),
            _text("Here's what I found."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "search kate bush"}]

        asyncio.run(run_tool_loop(
            client, registry, messages,

        ))

        tool_msgs = _get_tool_messages(messages)
        content = tool_msgs[0]["content"]

        # Must NOT be JSON
        self.assertNotIn('"results"', content)
        # Must have header with query and category
        first_line = content.split("\n")[0]
        self.assertIn("kate bush discography", first_line)
        self.assertIn("(category: general)", first_line)
        self.assertIn("1 results", first_line)
        # Must have aligned fields
        self.assertIn("title:", content)
        self.assertIn("url:", content)
        self.assertIn("content:", content)


class TestDefaultJsonFallback(unittest.TestCase):
    """Tools without custom formatting get default JSON serialisation."""

    def test_roon_status_gets_json(self):
        """roon_status output is serialised as JSON in the conversation."""
        client = _FakeLLMClient([
            _tool_call("roon_status", {"operation": "get_zones_status"}),
            _text("Playing Kate Bush."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "what's playing"}]

        asyncio.run(run_tool_loop(
            client, registry, messages,

        ))

        tool_msgs = _get_tool_messages(messages)
        content = tool_msgs[0]["content"]
        parsed = json.loads(content)
        self.assertEqual(parsed["operation"], "get_zones_status")
        self.assertIn("Kate Bush", parsed["result"])


class TestMultiToolStepPipeline(unittest.TestCase):
    """Multiple tool calls in different steps each get correct formatting."""

    def test_search_then_status_both_formatted(self):
        """Step 1: roon_search (compact), Step 2: roon_status (JSON)."""
        client = _FakeLLMClient([
            _tool_call("roon_search", {
                "operation": "new_search", "search_string": "Kate Bush",
            }, "call_1"),
            _tool_call("roon_status", {
                "operation": "get_zones_status",
            }, "call_2"),
            _text("All done."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "find kate bush then check status"}]

        asyncio.run(run_tool_loop(
            client, registry, messages,

        ))

        tool_msgs = _get_tool_messages(messages)
        self.assertEqual(len(tool_msgs), 2)

        # First is compact roon_search
        self.assertIn("(1) [5661c] Kate Bush", tool_msgs[0]["content"])
        self.assertNotIn("{", tool_msgs[0]["content"])

        # Second is JSON roon_status
        parsed = json.loads(tool_msgs[1]["content"])
        self.assertEqual(parsed["operation"], "get_zones_status")


if __name__ == "__main__":
    unittest.main()
