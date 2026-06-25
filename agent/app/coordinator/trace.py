from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.constants import CONTEXT_MAX_LIST_ITEMS, CONTEXT_MAX_STRING_LENGTH
from app.time_utils import format_relative_time, local_now


def pretty_json(data: Any) -> str:
    return json.dumps(data, indent=2)


def compact_for_context(value: Any, runtime_state: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) <= CONTEXT_MAX_STRING_LENGTH:
            return value
        return value[: CONTEXT_MAX_STRING_LENGTH - 3] + "..."

    if isinstance(value, list):
        total = len(value)
        if total <= CONTEXT_MAX_LIST_ITEMS:
            return [
                compact_for_context(item, runtime_state, depth + 1)
                for item in value
            ]
        handle_id = runtime_state.store_result_handle(value)
        return {
            "truncated": True,
            "total_count": total,
            "result_handle": handle_id,
        }

    if isinstance(value, dict):
        return {
            key: compact_for_context(val, runtime_state, depth + 1)
            for key, val in value.items()
        }

    return value


def _annotate_with_age(entry: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Add an ``age`` field ("N hr ago" / "just now" / ...) to a trace
    entry whose ``timestamp`` is an ISO-format string, and reformat the
    timestamp itself to a readable ``YYYY-MM-DD HH:MM`` form. Entries
    without a ``timestamp`` (legacy / test fixtures) are returned
    unchanged — better to omit the age than fabricate one."""
    ts_str = entry.get("timestamp")
    if not ts_str:
        return entry
    try:
        ts = datetime.fromisoformat(ts_str)
    except (TypeError, ValueError):
        return entry
    annotated = dict(entry)
    annotated["timestamp"] = ts.strftime("%Y-%m-%d %H:%M")
    annotated["age"] = format_relative_time((now - ts).total_seconds())
    return annotated


def _compact_search_output(entry: dict[str, Any]) -> dict[str, Any]:
    """Replace a roon_search tool_output with a compact summary."""
    output = entry.get("tool_output")
    if not output or not isinstance(output, dict):
        return entry
    description = output.get("description", "")
    # Trace entries use a flat "items" list (from _step_trace compaction);
    # count those directly.
    items = output.get("items")
    if isinstance(items, list):
        total_items = len(items)
    else:
        total_items = 0
    compacted = dict(entry)
    compacted["tool_output"] = {
        "description": description,
        "total_items": total_items,
    }
    return compacted


def build_trace_context(
    trace: list[dict[str, Any]],
    *,
    current_global_step: int = 0,
    full_results_window: int = 1,
    aggressive: bool = False,
) -> str:
    """Serialise the execution trace for injection into the coordinator prompt.

    *current_global_step* is the global step counter for the current turn.
    roon_search entries within *full_results_window* steps keep their full
    tool_output; older ones are compacted to description + count.

    When *aggressive* is True (small-model profiles), the trace is reduced
    to single-line summaries much earlier to cut context volume.
    """
    now = local_now()

    if aggressive:
        if len(trace) <= 1:
            return pretty_json([_annotate_with_age(e, now) for e in trace])
        lines: list[str] = []
        for step in trace[:-1]:
            skill = step.get("selected_skill") or "none"
            note = step.get("note") or ""
            summary_line = f"step {step.get('step', '?')}: {skill}"
            if note:
                summary_line += f" — {note[:120]}"
            lines.append(summary_line)
        summary = {
            "summary": {"total_steps": len(trace), "earlier_steps": lines[-4:]},
            "latest_step": _annotate_with_age(trace[-1], now),
        }
        return pretty_json(summary)

    # Apply full-results windowing: compact roon_search outputs older than N steps
    rendered = []
    for entry in trace:
        if (
            entry.get("selected_skill") == "roon_search"
            and entry.get("tool_output")
            and current_global_step - entry.get("global_step", 0) > full_results_window
        ):
            rendered.append(_annotate_with_age(_compact_search_output(entry), now))
        else:
            rendered.append(_annotate_with_age(entry, now))

    return pretty_json(rendered)
