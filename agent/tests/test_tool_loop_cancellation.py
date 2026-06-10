"""Cancellation contract tests for run_tool_loop.

Pins the behaviour of the ``cancel_event`` (threading.Event) parameter.
Today's cancellation model is thread-based — specific code paths poll
the event manually. These tests pin two invariants:

1. Pre-loop cancellation short-circuits immediately (no LLM call).
2. Cancellation set between steps raises before the next LLM call.

Prior to the inter-step check, a 10-step coordinator request would run
all 10 LLM calls even if the user hit Stop after step 1 — only the
tool itself (if it happened to poll ``cancel_event``) could abort
mid-request. The between-step test pins the fix.
"""

import asyncio
import threading
import unittest

from pydantic import BaseModel, Field

from app.exceptions import RequestInterrupted
from app.llm.client import LLMResponse, ToolCall
from app.llm.tool_loop import run_tool_loop
from app.llm.tool_registry import ToolRegistry


class _NoopInput(BaseModel):
    query: str = Field(default="")


class _NoopOutput(BaseModel):
    ok: bool = True


async def _noop_execute(_: _NoopInput) -> _NoopOutput:
    return _NoopOutput()


class _CountingLLMClient:
    """Fake LLM client: always returns the same tool call, counts calls."""

    def __init__(self, tool_name: str = "noop"):
        self._tool_name = tool_name
        self.call_count = 0

    async def completion(self, messages, tools=None, temperature=0.0):
        self.call_count += 1
        return LLMResponse(
            text=None,
            tool_calls=[ToolCall(id=f"c{self.call_count}", name=self._tool_name, arguments={})],
        )


class _NeverCalledClient:
    """Pre-loop cancellation should prevent any LLM calls at all."""

    def __init__(self):
        self.call_count = 0

    async def completion(self, messages, tools=None, temperature=0.0):
        self.call_count += 1
        raise AssertionError("LLM should not be called when cancel_event is pre-set")


def _make_registry(execute=_noop_execute) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("noop", "No-op tool", _NoopInput, execute)
    return reg


class TestToolLoopCancellation(unittest.TestCase):

    def test_cancel_before_loop_raises_immediately(self) -> None:
        """When the event is already set at entry, raise without any
        LLM call — the loop's first action is the cancel check.
        """
        cancel_event = threading.Event()
        cancel_event.set()

        client = _NeverCalledClient()
        registry = _make_registry()

        with self.assertRaises(RequestInterrupted):
            asyncio.run(run_tool_loop(
                client=client,
                registry=registry,
                messages=[{"role": "user", "content": "hi"}],
                cancel_event=cancel_event,
            ))

        self.assertEqual(client.call_count, 0)

    def test_cancel_set_after_tool_execution_breaks_before_next_llm_call(self) -> None:
        """The between-step gap: LLM returns a tool call, tool runs to
        completion, cancel_event fires (user hit Stop), next iteration
        must raise before calling the LLM again.

        Pre-fix: run_tool_loop had no cancel_event and would keep
        calling the LLM forever (until step limit). Post-fix: the
        check at the top of each iteration catches the event.
        """
        cancel_event = threading.Event()

        async def _set_cancel_during_tool(_: _NoopInput) -> _NoopOutput:
            # Simulate "the WS handler sets cancel_event while this
            # tool is running" — we set it from inside the tool so the
            # loop observes it on the next iteration's top-of-loop check.
            cancel_event.set()
            return _NoopOutput()

        client = _CountingLLMClient()
        registry = _make_registry(execute=_set_cancel_during_tool)

        with self.assertRaises(RequestInterrupted):
            asyncio.run(run_tool_loop(
                client=client,
                registry=registry,
                messages=[{"role": "user", "content": "hi"}],
                cancel_event=cancel_event,
            ))

        self.assertEqual(
            client.call_count, 1,
            "Loop should have raised before making a second LLM call",
        )

    def test_no_cancel_event_is_backwards_compatible(self) -> None:
        """Existing callers that don't pass cancel_event must still
        run normally.
        """
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="c1", name="noop", arguments={})],
            ),
            LLMResponse(text="done", tool_calls=[]),
        ]
        calls = iter(responses)

        class _ScriptedClient:
            call_count = 0

            async def completion(self, messages, tools=None, temperature=0.0):
                self.call_count += 1
                return next(calls)

        client = _ScriptedClient()
        registry = _make_registry()

        result = asyncio.run(run_tool_loop(
            client=client,
            registry=registry,
            messages=[{"role": "user", "content": "hi"}],
        ))

        self.assertEqual(result.text, "done")
        self.assertEqual(result.terminated_by, "completion")
        self.assertEqual(client.call_count, 2)

    def test_cancel_event_none_is_backwards_compatible(self) -> None:
        """Explicitly passing cancel_event=None (not just omitting the
        kwarg) must also work the same as omitting it.
        """
        responses = iter([
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="c1", name="noop", arguments={})],
            ),
            LLMResponse(text="done", tool_calls=[]),
        ])

        class _ScriptedClient:
            call_count = 0

            async def completion(self, messages, tools=None, temperature=0.0):
                self.call_count += 1
                return next(responses)

        registry = _make_registry()
        result = asyncio.run(run_tool_loop(
            client=_ScriptedClient(),
            registry=registry,
            messages=[{"role": "user", "content": "hi"}],
            cancel_event=None,
        ))
        self.assertEqual(result.text, "done")


if __name__ == "__main__":
    unittest.main()
