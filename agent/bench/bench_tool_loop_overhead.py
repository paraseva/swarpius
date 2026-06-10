"""Microbenchmark for per-tool-call overhead in tool_loop._execute_one.

Compares the current ``asyncio.to_thread(asyncio.run, coro)`` pattern
against a plain ``await coro`` control, and a ``to_thread(sync_body)``
variant that reuses the outer event loop. Measures the overhead of
executing a trivial async stub — the tool itself does nothing, so
the time reported is the dispatch cost.

Not a pytest — the filename doesn't start with ``test_``, so pytest's
default collection ignores it. Run manually from the agent root:

    TMPDIR=$TMPDIR/pytest-swarpius .venv-wsl/bin/python bench/bench_tool_loop_overhead.py

Output reports median, p99, and total wall-clock time for each
variant, at a default iteration count of 500.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from typing import Callable

# Make the agent's package importable when running this script directly.
# This file lives at agent/bench/bench_tool_loop_overhead.py, so the
# agent root is the parent of this file's parent.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from pydantic import BaseModel

from app.llm.tool_registry import ToolRegistry

ITERATIONS = 500


class _NoopInput(BaseModel):
    pass


class _NoopOutput(BaseModel):
    ok: bool = True


async def _noop_execute(_: _NoopInput) -> _NoopOutput:
    return _NoopOutput()


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("noop", "benchmark stub", _NoopInput, _noop_execute)
    return reg


# ───────────────────────────────────────────────────────────────────
# Variant A — current pattern: asyncio.to_thread(asyncio.run, coro)
# ───────────────────────────────────────────────────────────────────

async def _variant_a_to_thread_asyncio_run(registry: ToolRegistry) -> None:
    await asyncio.to_thread(
        asyncio.run, registry.execute("noop", {}),
    )


# ───────────────────────────────────────────────────────────────────
# Variant B — plain await (no thread, no new loop)
# ───────────────────────────────────────────────────────────────────

async def _variant_b_await(registry: ToolRegistry) -> None:
    await registry.execute("noop", {})


# ───────────────────────────────────────────────────────────────────
# Variant C — thread-local persistent event loop via run_in_executor
# ───────────────────────────────────────────────────────────────────

_thread_local = __import__("threading").local()


def _get_thread_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_local.loop = loop
    return loop


def _run_on_thread_loop(coro) -> object:
    loop = _get_thread_loop()
    return loop.run_until_complete(coro)


async def _variant_c_thread_local_loop(registry: ToolRegistry) -> None:
    await asyncio.to_thread(_run_on_thread_loop, registry.execute("noop", {}))


# ───────────────────────────────────────────────────────────────────
# Harness
# ───────────────────────────────────────────────────────────────────

async def _time_variant(
    name: str,
    invoke: Callable[[ToolRegistry], "asyncio.Future"],
    iterations: int,
) -> None:
    registry = _make_registry()
    # Warm up — first call pays any one-time setup cost (loop init, etc.)
    for _ in range(10):
        await invoke(registry)

    samples_ms: list[float] = []
    t_total_start = time.perf_counter()
    for _ in range(iterations):
        t0 = time.perf_counter()
        await invoke(registry)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
    total_s = time.perf_counter() - t_total_start

    samples_ms.sort()
    median = statistics.median(samples_ms)
    p99 = samples_ms[int(len(samples_ms) * 0.99)]
    mean = statistics.fmean(samples_ms)

    print(
        f"  {name:60s}  "
        f"median={median:6.3f} ms  mean={mean:6.3f} ms  p99={p99:6.3f} ms  "
        f"total={total_s:5.2f} s",
    )


async def main() -> None:
    print(f"Benchmarking _execute_one patterns ({ITERATIONS} iterations each)")
    print()
    await _time_variant(
        "A: asyncio.to_thread(asyncio.run, coro)   [current pattern]",
        _variant_a_to_thread_asyncio_run,
        ITERATIONS,
    )
    await _time_variant(
        "B: await coro                             [no thread, no new loop]",
        _variant_b_await,
        ITERATIONS,
    )
    await _time_variant(
        "C: asyncio.to_thread(persistent_loop)     [proposed: reuse loop per thread]",
        _variant_c_thread_local_loop,
        ITERATIONS,
    )


if __name__ == "__main__":
    asyncio.run(main())
