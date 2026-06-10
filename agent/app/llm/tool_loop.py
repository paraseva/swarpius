"""Core tool-calling loop: assemble prompt → call LLM → execute tools → repeat.

This module contains the main loop that drives the native tool-calling
flow.  It handles step limits, loop detection, and soft nudges.

When ``parallel`` is True, parallelisable tool calls within a step are
dispatched concurrently via ``asyncio.gather`` + ``asyncio.to_thread``.
Non-parallelisable calls run sequentially.  ``ROON_MAX_PARALLEL`` limits
the number of concurrent operations per batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.exceptions import RequestInterrupted
from app.llm.client import LLMClient, LLMResponse
from app.llm.tool_registry import ToolRegistry

_log = logging.getLogger("swarpius.tool_loop")

DEFAULT_HARD_STEP_LIMIT = 12
DEFAULT_SOFT_NUDGE_STEP = 8

# System-message content injected when the same tool + args fires twice
# in a row. Exposed as a constant so tests can reference it by name
# instead of hard-coding substrings.
LOOP_DETECTED_NUDGE = (
    "You already tried this exact approach and got the same result. "
    "Try a different approach or respond to the user."
)


@dataclass
class ToolExecution:
    """Record of a single tool call and its result within a loop run."""

    tool_name: str
    arguments: dict
    result: Any
    duration_ms: int
    error: Optional[str] = None


@dataclass
class LoopResult:
    """The final outcome of a tool-calling loop run."""

    text: Optional[str] = None
    tool_executions: List[ToolExecution] = field(default_factory=list)
    steps: int = 0
    terminated_by: str = "completion"  # completion | step_limit | error
    total_usage: Dict[str, int] = field(default_factory=lambda: {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    })


def _detect_loop(history: List[ToolExecution]) -> bool:
    """Return True if the last two tool calls are identical (name + args)."""
    if len(history) < 2:
        return False
    a, b = history[-2], history[-1]
    return a.tool_name == b.tool_name and a.arguments == b.arguments


def _accumulate_usage(total: Dict[str, Any], step_usage: Dict[str, Any]) -> None:
    for key in (
        "input_tokens", "output_tokens", "total_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    ):
        total[key] = total.get(key, 0) + step_usage.get(key, 0)
    # cost_usd is a float, provider-computed, may be None for unknown models
    step_cost = step_usage.get("cost_usd") or 0.0
    total["cost_usd"] = total.get("cost_usd", 0.0) + float(step_cost)


def _is_parallel_enabled() -> bool:
    from app.settings import get_settings
    return get_settings().parallel_tools


def _get_max_parallel() -> int | None:
    """Return the ROON_MAX_PARALLEL batch size, or None for unlimited.

    Resolved through the locked-at-startup settings cache: positive
    integer → use it; values < 1 → unlimited (None).
    """
    from app.settings import get_settings
    return get_settings().roon_max_parallel_batch


async def _execute_one(
    tc: Any,
    registry: ToolRegistry,
) -> tuple:
    """Execute a single tool call in a thread and return (tc, execution)."""
    started = time.perf_counter()
    try:
        tool_output = await asyncio.to_thread(
            asyncio.run, registry.execute(tc.name, tc.arguments),
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        execution = ToolExecution(
            tool_name=tc.name,
            arguments=tc.arguments,
            result=tool_output,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        execution = ToolExecution(
            tool_name=tc.name,
            arguments=tc.arguments,
            result=None,
            duration_ms=duration_ms,
            error=str(exc),
        )
    return tc, execution


async def _execute_sequential_group(
    calls: list,
    registry: ToolRegistry,
) -> List[tuple]:
    """Execute a group of tool calls sequentially, each in its own thread."""
    results = []
    for tc in calls:
        results.append(await _execute_one(tc, registry))
    return results


async def _execute_tools_parallel(
    tool_calls: list,
    registry: ToolRegistry,
) -> List[tuple]:
    """Execute tool calls with parallelism where safe.

    Parallelisable calls are dispatched concurrently; non-parallelisable
    calls form a single sequential group.  When ``ROON_MAX_PARALLEL`` is
    set, parallel-safe calls are chunked into batches of that size —
    batches run sequentially, calls within a batch run concurrently.

    Returns results in the original tool_call order.
    """
    max_parallel = _get_max_parallel()

    parallel_calls = []
    sequential_calls = []
    for tc in tool_calls:
        if registry.is_parallel_safe(tc.name):
            parallel_calls.append(tc)
        else:
            sequential_calls.append(tc)

    all_results: Dict[str, tuple] = {}

    async def _run_parallel_batches():
        if not parallel_calls:
            return
        if max_parallel and max_parallel < len(parallel_calls):
            for i in range(0, len(parallel_calls), max_parallel):
                batch = parallel_calls[i:i + max_parallel]
                batch_results = await asyncio.gather(
                    *[_execute_one(tc, registry) for tc in batch],
                )
                for item in batch_results:
                    all_results[item[0].id] = item
        else:
            batch_results = await asyncio.gather(
                *[_execute_one(tc, registry) for tc in parallel_calls],
            )
            for item in batch_results:
                all_results[item[0].id] = item

    async def _run_sequential_group():
        if not sequential_calls:
            return
        for item in await _execute_sequential_group(
            sequential_calls, registry,
        ):
            all_results[item[0].id] = item

    await asyncio.gather(_run_parallel_batches(), _run_sequential_group())

    # Return in original tool_call order
    return [all_results[tc.id] for tc in tool_calls]


async def run_tool_loop(
    client: LLMClient,
    registry: ToolRegistry,
    messages: List[Dict[str, Any]],
    tools: Optional[List[dict]] = None,
    hard_limit: int = DEFAULT_HARD_STEP_LIMIT,
    soft_nudge_step: int = DEFAULT_SOFT_NUDGE_STEP,
    on_tool_start: Optional[Callable[[str, str, dict, int], None]] = None,
    on_tool_end: Optional[Callable[[str, str, dict, Any, int, int, Optional[str]], None]] = None,
    on_llm_request_start: Optional[Callable[[int], None]] = None,
    on_llm_response: Optional[Callable[[LLMResponse, int], None]] = None,
    on_store_results: Optional[Callable[[list], List[str]]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> LoopResult:
    """Run the tool-calling loop until the model produces text or limits hit.

    Parameters
    ----------
    client : LLMClient
        The LLM client to use for completions.
    registry : ToolRegistry
        Registered tools for dispatch.
    messages : list
        The initial message list (system + user). Modified in-place as the
        loop appends assistant and tool messages.
    tools : list or None
        Tool schemas for the LLM.  If None, uses ``registry.to_tool_schemas()``.
    hard_limit : int
        Maximum number of loop iterations before forcing termination.
    soft_nudge_step : int
        Step at which a nudge message is injected.
    on_tool_start : callback(tool_call_id, tool_name, args, step)
        Called before each tool execution.
    on_tool_end : callback(tool_call_id, tool_name, args, result, step, duration_ms, error)
        Called after each tool execution.
    on_llm_request_start : callback(step)
        Called immediately before each LLM API call. Lets observers
        signal "Thinking" before the network round-trip begins.
    on_llm_response : callback(response, step)
        Called after each LLM completion.
    on_store_results : callback(entries) -> list[str]
        Called with result entries to store; returns allocated handles.
        Handles are passed to ``compact_output`` so the tool can embed
        ``[Result handle: ...]`` markers inline.

    Returns
    -------
    LoopResult
        Contains the final text response, tool execution history,
        step count, and aggregated token usage.
    """
    if tools is None:
        tools = registry.to_tool_schemas()

    parallel = _is_parallel_enabled()
    result = LoopResult()

    for step in range(1, hard_limit + 1):
        # Between-step cancellation check. The cancel_event is a
        # threading.Event set by the WS handler when an interrupt
        # arrives; individual tools also poll it internally for
        # mid-tool aborts. Without this check, a multi-step request
        # would run to step_limit even if the user had hit Stop after
        # step 1 — only the currently-executing tool would observe it.
        if cancel_event is not None and cancel_event.is_set():
            raise RequestInterrupted(f"Cancelled before step {step}.")

        result.steps = step

        if step == soft_nudge_step:
            messages.append({
                "role": "system",
                "content": (
                    f"You have used {step} of {hard_limit} available steps. "
                    "If you are stuck, consider responding to the user with what you have."
                ),
            })

        if on_llm_request_start:
            on_llm_request_start(step)
        response = await client.completion(messages=messages, tools=tools)
        _accumulate_usage(result.total_usage, response.usage)

        if on_llm_response:
            on_llm_response(response, step)

        if not response.has_tool_calls:
            result.text = response.text
            result.terminated_by = "completion"
            return result

        # Append assistant message with tool calls (for conversation continuity)
        assistant_msg: Dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": []}
        for tc in response.tool_calls:
            assistant_msg["tool_calls"].append({
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            })
        messages.append(assistant_msg)

        if on_tool_start:
            for tc in response.tool_calls:
                on_tool_start(tc.id, tc.name, tc.arguments, step)

        # ── Execute tool calls ────────────────────────────────
        if parallel and len(response.tool_calls) > 1:
            executed = await _execute_tools_parallel(
                response.tool_calls, registry,
            )
        else:
            executed = await _execute_sequential_group(
                response.tool_calls, registry,
            )

        # ── Post-process: store results, compact, append ─────
        for tc, execution in executed:
            result.tool_executions.append(execution)

            handles: List[str] = []
            if execution.result is not None and on_store_results:
                entries = registry.get_result_entries(
                    tc.name, tc.arguments, execution.result,
                )
                if entries:
                    handles = on_store_results(entries)

            if execution.error:
                output_text = json.dumps({"error": execution.error})
            else:
                output_text = registry.compact_output(
                    tc.name, execution.result, handles=handles or None,
                )

            if on_tool_end:
                on_tool_end(tc.id, tc.name, tc.arguments, execution.result, step, execution.duration_ms, execution.error)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": output_text,
            })

        if _detect_loop(result.tool_executions):
            messages.append({
                "role": "system",
                "content": LOOP_DETECTED_NUDGE,
            })
            _log.info("Loop detected at step %d: %s", step, result.tool_executions[-1].tool_name)

    result.terminated_by = "step_limit"
    _log.warning("Tool loop hit hard step limit (%d)", hard_limit)

    # One final call with no tools to force a text response
    response = await client.completion(messages=messages, tools=None)
    _accumulate_usage(result.total_usage, response.usage)
    result.text = response.text
    return result
