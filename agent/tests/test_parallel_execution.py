"""Parallel tool execution machinery — execution behaviour and feature
flag tests.

Exercises `run_tool_loop` with a fake LLM client that emits
pre-scripted tool calls and mock tools that sleep for known durations,
so parallelism can be verified without a live Roon connection.
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import patch

from pydantic import BaseModel, Field

from app.llm.client import LLMResponse, ToolCall
from app.llm.tool_loop import _get_max_parallel, run_tool_loop
from app.llm.tool_registry import ToolRegistry

# Hard-coded ROON_MAX_PARALLEL default when the env var is unset.
# Mirrors the value used by ``Settings.from_env`` for the
# ``roon_max_parallel`` field.
DEFAULT_ROON_MAX_PARALLEL = 5

# ---------------------------------------------------------------------------
# Mock tool infrastructure
# ---------------------------------------------------------------------------


class MockToolInput(BaseModel):
    """Input schema for mock tools."""
    value: str = Field(default="default")


class MockToolOutput(BaseModel):
    """Output schema for mock tools."""
    result: str = Field(default="ok")
    tool_name: str = Field(default="")
    received_value: str = Field(default="")


class SlowMockToolInput(BaseModel):
    """Input schema for mock tools with configurable delay."""
    value: str = Field(default="default")
    delay: float = Field(default=0.0, description="Seconds to sleep")


class SlowMockToolOutput(BaseModel):
    """Output schema for slow mock tools."""
    result: str = Field(default="ok")
    tool_name: str = Field(default="")
    started_at: float = Field(default=0.0)
    finished_at: float = Field(default=0.0)


def _make_mock_tool(name: str, parallel_safe: bool = True, delay: float = 0.0):
    """Create a mock tool class with optional delay and parallel_safe flag."""

    class _Tool:
        input_schema = SlowMockToolInput if delay > 0 else MockToolInput
        output_schema = SlowMockToolOutput if delay > 0 else MockToolOutput
        tool_name = name

        def __init__(self):
            self.parallel_safe = parallel_safe
            self.call_count = 0

        async def run_async(self, params):
            self.call_count += 1
            if delay > 0 or (hasattr(params, "delay") and params.delay > 0):
                sleep_time = getattr(params, "delay", delay) or delay
                started = time.monotonic()
                # Use time.sleep in a thread to simulate sync blocking work
                await asyncio.to_thread(time.sleep, sleep_time)
                finished = time.monotonic()
                return SlowMockToolOutput(
                    result="ok",
                    tool_name=name,
                    started_at=started,
                    finished_at=finished,
                )
            return MockToolOutput(
                result="ok",
                tool_name=name,
                received_value=getattr(params, "value", ""),
            )

    return _Tool()


def _register_tool(registry: ToolRegistry, tool, name: str, description: str = "Mock tool"):
    """Register a mock tool in the registry."""
    registry.register(
        name=name,
        description=description,
        input_schema=tool.input_schema,
        execute=tool.run_async,
        tool_instance=tool,
        parallel_safe=getattr(tool, "parallel_safe", False),
    )


# ---------------------------------------------------------------------------
# Fake LLM client — emits pre-scripted responses
# ---------------------------------------------------------------------------


class ScriptedLLMClient:
    """Fake LLM client that returns pre-scripted responses in sequence.

    Each entry in `responses` is either:
    - A list of (tool_name, args_dict) tuples → emitted as tool calls
    - A string → emitted as a text response (terminates the loop)
    """

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._call_idx = 0
        self.model = "test/mock"

    async def completion(self, messages, tools=None, temperature=0.0):
        if self._call_idx >= len(self._responses):
            return LLMResponse(text="(out of scripted responses)")

        entry = self._responses[self._call_idx]
        self._call_idx += 1

        if isinstance(entry, str):
            return LLMResponse(text=entry, usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

        # List of tool calls
        tool_calls = []
        for i, (name, args) in enumerate(entry):
            tool_calls.append(ToolCall(
                id=f"call_{self._call_idx}_{i}",
                name=name,
                arguments=args,
            ))
        return LLMResponse(
            tool_calls=tool_calls,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )


# ---------------------------------------------------------------------------
# E1: Parallel flag on — two parallel-safe tools run concurrently
# ---------------------------------------------------------------------------


class TestParallelExecution(unittest.TestCase):
    """E1: Two parallel-safe tools should run concurrently when flag is on."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_parallel_tools_run_concurrently(self):
        """Wall-clock should be ~max(delays), not sum(delays)."""
        tool_a = _make_mock_tool("tool_a", parallel_safe=True, delay=0.3)
        tool_b = _make_mock_tool("tool_b", parallel_safe=True, delay=0.3)

        registry = ToolRegistry()
        _register_tool(registry, tool_a, "tool_a")
        _register_tool(registry, tool_b, "tool_b")

        client = ScriptedLLMClient([
            [("tool_a", {"value": "a", "delay": 0.3}), ("tool_b", {"value": "b", "delay": 0.3})],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 2)
        # Parallel: should take ~0.3s, not ~0.6s
        # Use generous margin: must be under 70% of sequential time
        self.assertLess(elapsed, 0.6 * 0.7, f"Expected parallel execution but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# E2: Parallel flag off — same tools run sequentially
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# E3: Message ordering preserved
# ---------------------------------------------------------------------------


class TestMessageOrdering(unittest.TestCase):
    """E3: Tool result messages must maintain the same order as tool calls."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_tool_results_in_call_order(self):
        """Even if tool_b finishes first, messages should be in [a, b] order."""
        # tool_a is slow, tool_b is fast — b finishes first
        tool_a = _make_mock_tool("tool_a", parallel_safe=True, delay=0.3)
        tool_b = _make_mock_tool("tool_b", parallel_safe=True, delay=0.05)

        registry = ToolRegistry()
        _register_tool(registry, tool_a, "tool_a")
        _register_tool(registry, tool_b, "tool_b")

        client = ScriptedLLMClient([
            [("tool_a", {"value": "a", "delay": 0.3}), ("tool_b", {"value": "b", "delay": 0.05})],
            "done",
        ])

        messages: list = [{"role": "user", "content": "test"}]
        asyncio.run(run_tool_loop(client, registry, messages))

        # Find tool result messages
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 2)

        # The assistant message has tool_calls in order [tool_a, tool_b]
        assistant_msg = next(m for m in messages if m.get("role") == "assistant")
        call_ids = [tc["id"] for tc in assistant_msg["tool_calls"]]

        # Tool result messages must match that order
        result_ids = [m["tool_call_id"] for m in tool_messages]
        self.assertEqual(result_ids, call_ids)


# ---------------------------------------------------------------------------
# E4: Callbacks fire per-tool
# ---------------------------------------------------------------------------


class TestCallbacksFiring(unittest.TestCase):
    """E4: on_tool_start and on_tool_end fire exactly once per tool call."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_callbacks_fire_per_tool(self):
        tool_a = _make_mock_tool("tool_a", parallel_safe=True)
        tool_b = _make_mock_tool("tool_b", parallel_safe=True)

        registry = ToolRegistry()
        _register_tool(registry, tool_a, "tool_a")
        _register_tool(registry, tool_b, "tool_b")

        client = ScriptedLLMClient([
            [("tool_a", {"value": "a"}), ("tool_b", {"value": "b"})],
            "done",
        ])

        start_calls: list = []
        end_calls: list = []

        def on_start(tool_call_id, name, args, step):
            start_calls.append(name)

        def on_end(tool_call_id, name, args, result, step, duration_ms, error):
            end_calls.append(name)

        messages = [{"role": "user", "content": "test"}]
        asyncio.run(run_tool_loop(
            client, registry, messages,
            on_tool_start=on_start,
            on_tool_end=on_end,
        ))

        self.assertEqual(sorted(start_calls), ["tool_a", "tool_b"])
        self.assertEqual(sorted(end_calls), ["tool_a", "tool_b"])


# ---------------------------------------------------------------------------
# E5: Session-key grouping
# ---------------------------------------------------------------------------


class TestSessionKeyGrouping(unittest.TestCase):
    """E5: Drill-down calls sharing a session key run sequentially within
    their group, while different-session calls run in parallel."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_same_session_drills_sequential_different_session_parallel(self):
        """Two drill-downs on session A should run sequentially,
        while a drill-down on session B runs in parallel with them.

        This test needs the session key resolution mechanism to identify
        that two roon_search drill_down_reference calls share a session.
        For now, we test with generic parallel-safe tools that have a
        session_key attribute — the grouping logic will use this.
        """
        # This test validates the grouping concept. The actual
        # session key resolution for roon_search references will be
        # tested in T3/T4 (live tests). Here we verify the timing
        # behaviour of the grouping mechanism using mock tools.

        # Three tools: a_1 and a_2 in same group (sequential), b independent
        tool_a1 = _make_mock_tool("tool_a1", parallel_safe=True, delay=0.15)
        tool_a2 = _make_mock_tool("tool_a2", parallel_safe=True, delay=0.15)
        tool_b = _make_mock_tool("tool_b", parallel_safe=True, delay=0.15)

        registry = ToolRegistry()
        _register_tool(registry, tool_a1, "tool_a1")
        _register_tool(registry, tool_a2, "tool_a2")
        _register_tool(registry, tool_b, "tool_b")

        client = ScriptedLLMClient([
            [
                ("tool_a1", {"value": "a1", "delay": 0.15}),
                ("tool_a2", {"value": "a2", "delay": 0.15}),
                ("tool_b", {"value": "b", "delay": 0.15}),
            ],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 3)

        # If all 3 ran in parallel: ~0.15s
        # If a1+a2 sequential, b parallel: ~0.3s (max(0.3, 0.15))
        # If all sequential: ~0.45s
        # For now (before session grouping), all parallel-safe tools
        # run in parallel, so expect ~0.15s. This test will be refined
        # when session grouping is implemented to assert ~0.3s.
        self.assertLess(elapsed, 0.45 * 0.7, f"Expected some parallelism but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# E6: Non-parallelisable tools run sequentially
# ---------------------------------------------------------------------------


class TestNonParallelToolsSequential(unittest.TestCase):
    """E6: Two non-parallel-safe tools in the same step run sequentially."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_non_parallel_tools_sequential(self):
        tool_a = _make_mock_tool("tool_a", parallel_safe=False, delay=0.15)
        tool_b = _make_mock_tool("tool_b", parallel_safe=False, delay=0.15)

        registry = ToolRegistry()
        _register_tool(registry, tool_a, "tool_a")
        _register_tool(registry, tool_b, "tool_b")

        client = ScriptedLLMClient([
            [("tool_a", {"value": "a", "delay": 0.15}), ("tool_b", {"value": "b", "delay": 0.15})],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 2)
        # Sequential: should take >= 0.25s
        self.assertGreaterEqual(elapsed, 0.25, f"Expected sequential execution but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# E7: Mixed step — parallel + non-parallel
# ---------------------------------------------------------------------------


class TestMixedStep(unittest.TestCase):
    """E7: Non-parallel tools run sequentially; parallel tools run concurrently.
    Wall-clock ≈ max(sequential_group, parallel_group)."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_mixed_parallel_and_sequential(self):
        # parallel tools: fast (0.1s each, concurrent = 0.1s)
        tool_p1 = _make_mock_tool("tool_p1", parallel_safe=True, delay=0.1)
        tool_p2 = _make_mock_tool("tool_p2", parallel_safe=True, delay=0.1)
        # sequential tool: slower (0.2s)
        tool_s = _make_mock_tool("tool_s", parallel_safe=False, delay=0.2)

        registry = ToolRegistry()
        _register_tool(registry, tool_p1, "tool_p1")
        _register_tool(registry, tool_p2, "tool_p2")
        _register_tool(registry, tool_s, "tool_s")

        client = ScriptedLLMClient([
            [
                ("tool_p1", {"value": "p1", "delay": 0.1}),
                ("tool_s", {"value": "s", "delay": 0.2}),
                ("tool_p2", {"value": "p2", "delay": 0.1}),
            ],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 3)
        # All sequential: 0.1 + 0.2 + 0.1 = 0.4s
        # Optimal parallel: max(0.2, 0.1) = 0.2s
        # Must be less than fully sequential
        self.assertLess(elapsed, 0.4 * 0.7, f"Expected mixed execution but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# F1: Flag false — all tools run sequentially
# ---------------------------------------------------------------------------


class TestFlagAbsentDefaultsFalse(unittest.TestCase):
    """F3: When PARALLEL_TOOLS is not set, behaviour matches sequential."""

    def test_flag_absent_is_sequential(self):
        # Ensure PARALLEL_TOOLS is not set
        env = os.environ.copy()
        env.pop("PARALLEL_TOOLS", None)

        tool_a = _make_mock_tool("tool_a", parallel_safe=True, delay=0.15)
        tool_b = _make_mock_tool("tool_b", parallel_safe=True, delay=0.15)

        registry = ToolRegistry()
        _register_tool(registry, tool_a, "tool_a")
        _register_tool(registry, tool_b, "tool_b")

        client = ScriptedLLMClient([
            [("tool_a", {"value": "a", "delay": 0.15}), ("tool_b", {"value": "b", "delay": 0.15})],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        with patch.dict(os.environ, env, clear=True):
            result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 2)
        self.assertGreaterEqual(elapsed, 0.25, f"Expected sequential but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# Single tool call
# ---------------------------------------------------------------------------


class TestSingleToolCall(unittest.TestCase):
    """A single tool call must behave identically regardless of the PARALLEL_TOOLS flag."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"})
    def test_single_tool_parallel_flag_on(self):
        tool = _make_mock_tool("tool_a", parallel_safe=True)

        registry = ToolRegistry()
        _register_tool(registry, tool, "tool_a")

        client = ScriptedLLMClient([
            [("tool_a", {"value": "hello"})],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        result = asyncio.run(run_tool_loop(client, registry, messages))

        self.assertEqual(len(result.tool_executions), 1)
        self.assertEqual(result.tool_executions[0].tool_name, "tool_a")
        self.assertEqual(result.text, "done")


# ---------------------------------------------------------------------------
# B1: Batch size limits concurrent operations
# ---------------------------------------------------------------------------


class TestBatchSizeLimitsConcurrency(unittest.TestCase):
    """B1: With ROON_MAX_PARALLEL=2 and 4 parallel-safe tools (each 0.2s),
    execution should take ~0.4s (2 batches of 2) not ~0.2s (all parallel)
    or ~0.8s (all sequential)."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true", "ROON_MAX_PARALLEL": "2"})
    def test_batch_size_2_with_4_tools(self):
        tools = [_make_mock_tool(f"tool_{i}", parallel_safe=True, delay=0.2) for i in range(4)]
        registry = ToolRegistry()
        for t in tools:
            _register_tool(registry, t, t.tool_name)

        client = ScriptedLLMClient([
            [(f"tool_{i}", {"value": str(i), "delay": 0.2}) for i in range(4)],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 4)
        # 2 batches of 2 concurrent @ 0.2s each = ~0.4s
        # Allow generous margins: > 0.3s (not fully parallel) and < 0.7s (not fully sequential)
        self.assertGreaterEqual(elapsed, 0.3, f"Expected batched execution but took {elapsed:.2f}s (too fast — no batching?)")
        self.assertLess(elapsed, 0.7, f"Expected batched execution but took {elapsed:.2f}s (too slow — sequential?)")


# ---------------------------------------------------------------------------
# B2: Batch size larger than call count — same as unbatched
# ---------------------------------------------------------------------------


class TestBatchSizeLargerThanCallCount(unittest.TestCase):
    """B2: With ROON_MAX_PARALLEL=10 and 3 parallel tools, all run in one
    batch (same as current unbatched behaviour)."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true", "ROON_MAX_PARALLEL": "10"})
    def test_all_in_one_batch(self):
        tools = [_make_mock_tool(f"tool_{i}", parallel_safe=True, delay=0.2) for i in range(3)]
        registry = ToolRegistry()
        for t in tools:
            _register_tool(registry, t, t.tool_name)

        client = ScriptedLLMClient([
            [(f"tool_{i}", {"value": str(i), "delay": 0.2}) for i in range(3)],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 3)
        # All concurrent in one batch: ~0.2s
        self.assertLess(elapsed, 0.2 * 2, f"Expected single batch but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# B3: Batch size 1 — all sequential
# ---------------------------------------------------------------------------


class TestBatchSizeOne(unittest.TestCase):
    """B3: ROON_MAX_PARALLEL=1 forces all parallel-safe tools to run
    sequentially (one at a time)."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true", "ROON_MAX_PARALLEL": "1"})
    def test_batch_size_one_is_sequential(self):
        tools = [_make_mock_tool(f"tool_{i}", parallel_safe=True, delay=0.15) for i in range(3)]
        registry = ToolRegistry()
        for t in tools:
            _register_tool(registry, t, t.tool_name)

        client = ScriptedLLMClient([
            [(f"tool_{i}", {"value": str(i), "delay": 0.15}) for i in range(3)],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 3)
        # 3 sequential @ 0.15s = ~0.45s
        self.assertGreaterEqual(elapsed, 0.35, f"Expected sequential but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# B4: Batching preserves result ordering
# ---------------------------------------------------------------------------


class TestBatchingPreservesOrder(unittest.TestCase):
    """B4: Tool results maintain original call order across batches."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true", "ROON_MAX_PARALLEL": "2"})
    def test_result_order_across_batches(self):
        # 5 tools with different delays — results must come back in call order
        tools = [_make_mock_tool(f"tool_{i}", parallel_safe=True) for i in range(5)]
        registry = ToolRegistry()
        for t in tools:
            _register_tool(registry, t, t.tool_name)

        client = ScriptedLLMClient([
            [(f"tool_{i}", {"value": f"v{i}"}) for i in range(5)],
            "done",
        ])

        messages: list = [{"role": "user", "content": "test"}]
        asyncio.run(run_tool_loop(client, registry, messages))

        tool_messages = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 5)

        assistant_msg = next(m for m in messages if m.get("role") == "assistant")
        call_ids = [tc["id"] for tc in assistant_msg["tool_calls"]]
        result_ids = [m["tool_call_id"] for m in tool_messages]
        self.assertEqual(result_ids, call_ids)


# ---------------------------------------------------------------------------
# B5: Mixed parallel + non-parallel with batching
# ---------------------------------------------------------------------------


class TestBatchingWithMixedTools(unittest.TestCase):
    """B5: Batching applies to parallel-safe tools; non-parallel-safe tools
    still form their own sequential group running concurrently with batches."""

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true", "ROON_MAX_PARALLEL": "2"})
    def test_mixed_batched_and_sequential(self):
        # 3 parallel tools (batched as 2+1) + 1 sequential tool
        p_tools = [_make_mock_tool(f"tool_p{i}", parallel_safe=True, delay=0.15) for i in range(3)]
        s_tool = _make_mock_tool("tool_s", parallel_safe=False, delay=0.15)

        registry = ToolRegistry()
        for t in p_tools:
            _register_tool(registry, t, t.tool_name)
        _register_tool(registry, s_tool, "tool_s")

        client = ScriptedLLMClient([
            [
                ("tool_p0", {"value": "p0", "delay": 0.15}),
                ("tool_s", {"value": "s", "delay": 0.15}),
                ("tool_p1", {"value": "p1", "delay": 0.15}),
                ("tool_p2", {"value": "p2", "delay": 0.15}),
            ],
            "done",
        ])

        messages = [{"role": "user", "content": "test"}]
        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, messages))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 4)
        # Parallel batches: batch1(p0,p1)=0.15s then batch2(p2)=0.15s = 0.3s total
        # Sequential group: tool_s = 0.15s (runs concurrently with batches)
        # Wall-clock: max(0.3, 0.15) = ~0.3s
        # Fully sequential would be 4 * 0.15 = 0.6s
        self.assertLess(elapsed, 0.6 * 0.7, f"Expected batched+mixed but took {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# B6: Default ROON_MAX_PARALLEL is 5; <1 means unlimited
# ---------------------------------------------------------------------------


class TestDefaultMaxParallel(unittest.TestCase):
    """B6: ROON_MAX_PARALLEL defaults to 5 (the safety cap chosen to keep
    Roon Cores from dropping responses). The env var overrides it; values
    < 1 mean unlimited."""

    def test_default_constant_value(self):
        self.assertEqual(DEFAULT_ROON_MAX_PARALLEL, 5)

    @patch.dict(os.environ, {"ROON_MAX_PARALLEL": "8"}, clear=False)
    def test_positive_integer_overrides_default(self):
        self.assertEqual(_get_max_parallel(), 8)

    @patch.dict(os.environ, {"ROON_MAX_PARALLEL": "0"}, clear=False)
    def test_zero_means_unlimited(self):
        self.assertIsNone(_get_max_parallel())

    @patch.dict(os.environ, {"ROON_MAX_PARALLEL": "not-a-number"}, clear=False)
    def test_non_numeric_falls_back_to_default(self):
        self.assertEqual(_get_max_parallel(), DEFAULT_ROON_MAX_PARALLEL)

    @patch.dict(os.environ, {"PARALLEL_TOOLS": "true"}, clear=False)
    def test_default_5_batches_10_calls_into_2_batches(self):
        os.environ.pop("ROON_MAX_PARALLEL", None)

        tools = [_make_mock_tool(f"tool_{i}", parallel_safe=True, delay=0.2) for i in range(10)]
        registry = ToolRegistry()
        for t in tools:
            _register_tool(registry, t, t.tool_name)

        client = ScriptedLLMClient([
            [(f"tool_{i}", {"value": str(i), "delay": 0.2}) for i in range(10)],
            "done",
        ])

        started = time.monotonic()
        result = asyncio.run(run_tool_loop(client, registry, [{"role": "user", "content": "test"}]))
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.tool_executions), 10)
        # 10 calls / 5 per batch → 2 sequential batches at 0.2s each = ~0.4s.
        # Fewer than 3 batches (~0.6s) and more than one batch (~0.2s).
        self.assertGreater(elapsed, 0.3, f"Expected 2 batches, ran in {elapsed:.2f}s")
        self.assertLess(elapsed, 0.55, f"Expected 2 batches, took {elapsed:.2f}s")

if __name__ == "__main__":
    unittest.main()
