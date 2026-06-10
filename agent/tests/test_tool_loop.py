"""Tests for the tool-calling loop: step limits, loop detection, completion."""

import asyncio
import unittest
from typing import List

from pydantic import BaseModel, Field

from app.llm.client import LLMResponse, ToolCall
from app.llm.tool_loop import LOOP_DETECTED_NUDGE, run_tool_loop
from app.llm.tool_registry import ToolRegistry

# ------------------------------------------------------------------ #
#  Test fixtures                                                      #
# ------------------------------------------------------------------ #

class _SearchInput(BaseModel):
    query: str = Field(..., description="Search query")


class _SearchOutput(BaseModel):
    results: List[str]


async def _search_execute(params: _SearchInput) -> _SearchOutput:
    return _SearchOutput(results=[f"result for '{params.query}'"])


class _FakeLLMClient:
    """Fake LLM client that returns a scripted sequence of responses."""

    def __init__(self, responses: List[LLMResponse]):
        self._responses = list(responses)
        self._call_count = 0

    async def completion(self, messages, tools=None, temperature=0.0):
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]

    @property
    def call_count(self):
        return self._call_count


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


def _tool_response(name: str, args: dict, call_id: str = "call_1") -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("search", "Search for things", _SearchInput, _search_execute)
    return reg


# ------------------------------------------------------------------ #
#  Tests                                                              #
# ------------------------------------------------------------------ #

class TestToolLoopCompletion(unittest.TestCase):
    def test_immediate_text_response(self):
        """Model responds with text on first call — no tool use."""
        client = _FakeLLMClient([_text_response("Hello!")])
        registry = _make_registry()
        messages = [{"role": "user", "content": "hi"}]

        result = asyncio.run(run_tool_loop(client, registry, messages))

        self.assertEqual(result.text, "Hello!")
        self.assertEqual(result.steps, 1)
        self.assertEqual(result.terminated_by, "completion")
        self.assertEqual(len(result.tool_executions), 0)

    def test_tool_then_text(self):
        """Model calls a tool, gets result, then responds with text."""
        client = _FakeLLMClient([
            _tool_response("search", {"query": "jazz"}, "call_1"),
            _text_response("Found some jazz."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "find jazz"}]

        result = asyncio.run(run_tool_loop(client, registry, messages))

        self.assertEqual(result.text, "Found some jazz.")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.terminated_by, "completion")
        self.assertEqual(len(result.tool_executions), 1)
        self.assertEqual(result.tool_executions[0].tool_name, "search")

    def test_usage_accumulated(self):
        """Token usage is summed across all LLM calls."""
        client = _FakeLLMClient([
            _tool_response("search", {"query": "jazz"}, "call_1"),
            _text_response("Done."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "find jazz"}]

        result = asyncio.run(run_tool_loop(client, registry, messages))

        # 2 calls × 15 total tokens each
        self.assertEqual(result.total_usage["total_tokens"], 30)


class TestToolLoopStepLimit(unittest.TestCase):
    def test_hard_limit_forces_response(self):
        """When the hard limit is hit, the loop forces a text response."""
        # 3 tool calls (one per step) + the forced final text response
        responses = [
            _tool_response("search", {"query": f"q{i}"}, f"call_{i}")
            for i in range(3)
        ]
        responses.append(_text_response("I ran out of steps."))
        client = _FakeLLMClient(responses)
        registry = _make_registry()
        messages = [{"role": "user", "content": "keep searching"}]

        result = asyncio.run(run_tool_loop(client, registry, messages, hard_limit=3))

        self.assertEqual(result.terminated_by, "step_limit")
        self.assertEqual(result.text, "I ran out of steps.")
        self.assertEqual(result.steps, 3)

    def test_soft_nudge_injected(self):
        """A nudge message is injected at the soft_nudge_step."""
        client = _FakeLLMClient([
            _tool_response("search", {"query": "a"}, "call_1"),
            _tool_response("search", {"query": "b"}, "call_2"),
            _text_response("Done."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "search"}]

        asyncio.run(run_tool_loop(client, registry, messages, soft_nudge_step=2, hard_limit=10))

        # Check that a nudge system message was added
        system_msgs = [m for m in messages if m["role"] == "system" and "steps" in m.get("content", "")]
        self.assertEqual(len(system_msgs), 1)
        self.assertIn("2 of 10", system_msgs[0]["content"])


class TestToolLoopDetection(unittest.TestCase):
    def test_loop_detection_injects_warning(self):
        """Identical consecutive tool calls trigger a loop warning."""
        client = _FakeLLMClient([
            _tool_response("search", {"query": "jazz"}, "call_1"),
            _tool_response("search", {"query": "jazz"}, "call_2"),
            _text_response("Giving up."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "search"}]

        result = asyncio.run(run_tool_loop(client, registry, messages))

        # Should have a loop detection system message
        loop_msgs = [m for m in messages if m["role"] == "system" and m.get("content") == LOOP_DETECTED_NUDGE]
        self.assertEqual(len(loop_msgs), 1)
        self.assertEqual(result.text, "Giving up.")

    def test_different_args_no_loop_detection(self):
        """Different arguments should NOT trigger loop detection."""
        client = _FakeLLMClient([
            _tool_response("search", {"query": "jazz"}, "call_1"),
            _tool_response("search", {"query": "rock"}, "call_2"),
            _text_response("Done."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "search"}]

        asyncio.run(run_tool_loop(client, registry, messages))

        loop_msgs = [m for m in messages if m["role"] == "system" and m.get("content") == LOOP_DETECTED_NUDGE]
        self.assertEqual(len(loop_msgs), 0)


class TestToolLoopErrorHandling(unittest.TestCase):
    def test_tool_error_returned_to_model(self):
        """When a tool raises, the error is sent back as a tool result."""
        async def _failing_execute(params):
            raise RuntimeError("connection lost")

        registry = ToolRegistry()
        registry.register("broken", "A broken tool", _SearchInput, _failing_execute)

        client = _FakeLLMClient([
            _tool_response("broken", {"query": "test"}, "call_1"),
            _text_response("The tool failed."),
        ])
        messages = [{"role": "user", "content": "try it"}]

        result = asyncio.run(run_tool_loop(client, registry, messages))

        self.assertEqual(result.text, "The tool failed.")
        self.assertEqual(len(result.tool_executions), 1)
        self.assertIsNotNone(result.tool_executions[0].error)
        self.assertIn("connection lost", result.tool_executions[0].error)

        # Check error was sent as tool message
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("connection lost", tool_msgs[0]["content"])

    def test_llm_error_propagates_to_caller(self):
        """When the LLM client raises, the exception propagates out of
        run_tool_loop — the caller is responsible for categorising it
        (rate limit, auth, etc.). The loop does NOT swallow exceptions
        from completion() the way it does for tool-execution errors."""
        class _RaisingClient:
            model = "test/mock"
            async def completion(self, messages, tools=None, temperature=0.0):
                raise TimeoutError("provider timeout")

        registry = _make_registry()
        messages = [{"role": "user", "content": "hi"}]

        with self.assertRaises(TimeoutError) as ctx:
            asyncio.run(run_tool_loop(_RaisingClient(), registry, messages))
        self.assertIn("provider timeout", str(ctx.exception))

    def test_llm_error_after_first_step_still_propagates(self):
        """Even when earlier steps succeed, a raise on a later LLM call
        propagates out cleanly (no half-swallowed state)."""
        class _FailOnSecondCall:
            model = "test/mock"
            def __init__(self):
                self._n = 0
            async def completion(self, messages, tools=None, temperature=0.0):
                self._n += 1
                if self._n == 1:
                    return _tool_response("search", {"query": "jazz"}, "call_1")
                raise RuntimeError("provider blew up on step 2")

        registry = _make_registry()
        messages = [{"role": "user", "content": "search"}]

        with self.assertRaises(RuntimeError):
            asyncio.run(run_tool_loop(_FailOnSecondCall(), registry, messages))

        # The first tool execution should have been recorded in the
        # conversation before the second LLM call failed. The caller
        # can inspect `messages` to see what got through.
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)


class TestOnStoreResults(unittest.TestCase):
    def test_store_callback_receives_entries_and_returns_handles(self):
        """on_store_results is called with result entries and its returned
        handles are passed to compact_output."""
        # Tool with get_result_entries that returns one entry
        from app.runtime.result_store_types import ResultStoreEntry

        class _StorableTool:
            def get_result_entries(self, params, output):
                return [ResultStoreEntry(
                    items=[{"result": "data"}],
                    description='"test"',
                    item_count=1,
                    tool_name="search",
                )]

            def compact_output(self, output, handles=None):
                if handles:
                    return f"[handle:{handles[0]}] results"
                return "results"

        registry = ToolRegistry()
        registry.register("search", "Search", _SearchInput, _search_execute,
                          tool_instance=_StorableTool())

        stored = []

        def _store(entries):
            stored.extend(entries)
            return ["res_00001"]

        client = _FakeLLMClient([
            _tool_response("search", {"query": "jazz"}, "call_1"),
            _text_response("Done."),
        ])
        messages = [{"role": "user", "content": "search"}]

        asyncio.run(run_tool_loop(
            client, registry, messages, on_store_results=_store,
        ))

        # Store callback was called
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].description, '"test"')

        # Handle was passed to compact_output
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        self.assertIn("[handle:res_00001]", tool_msgs[0]["content"])

    def test_no_store_callback_still_works(self):
        """Without on_store_results, tool output is compacted normally."""
        client = _FakeLLMClient([
            _tool_response("search", {"query": "jazz"}, "call_1"),
            _text_response("Done."),
        ])
        registry = _make_registry()
        messages = [{"role": "user", "content": "search"}]

        asyncio.run(run_tool_loop(client, registry, messages))

        tool_msgs = [m for m in messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)

    def test_store_not_called_on_error(self):
        """on_store_results is not called when the tool raises."""
        async def _failing(params):
            raise RuntimeError("boom")

        registry = ToolRegistry()
        registry.register("broken", "broken", _SearchInput, _failing)
        client = _FakeLLMClient([
            _tool_response("broken", {"query": "x"}, "call_1"),
            _text_response("Failed."),
        ])
        messages = [{"role": "user", "content": "try"}]
        stored = []

        asyncio.run(run_tool_loop(
            client, registry, messages,
            on_store_results=lambda entries: stored.extend(entries) or [],
        ))

        self.assertEqual(stored, [])


if __name__ == "__main__":
    unittest.main()
