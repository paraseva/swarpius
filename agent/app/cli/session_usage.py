"""Session-level usage aggregator for CLI mode.

Per-request totals are formatted by ``app.cli_telemetry``; this
module accumulates across requests for the running session
counter that prints after each response and the ``/usage``
command's detailed breakdown.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from app.cli.telemetry import _fmt_cost, _fmt_duration


class SessionUsageTracker:
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cost_usd = 0.0
        self.steps = 0
        self.duration_ms = 0
        self.request_count = 0

    def accumulate(
        self,
        usage: Dict[str, Any],
        *,
        steps: int,
        duration_ms: int,
    ) -> None:
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.cache_read += int(usage.get("cache_read_input_tokens") or 0)
        self.cost_usd += float(usage.get("cost_usd") or 0.0)
        self.steps += steps
        self.duration_ms += duration_ms
        self.request_count += 1

    def has_data(self) -> bool:
        return self.request_count > 0

    def _format_totals(self) -> str:
        parts: list[str] = []
        if self.input_tokens:
            parts.append(f"{self.input_tokens:,} in")
        if self.output_tokens:
            parts.append(f"{self.output_tokens:,} out")
        if self.cache_read:
            parts.append(f"{self.cache_read:,} cached")
        if self.cost_usd > 0.0:
            parts.append(_fmt_cost(self.cost_usd))
        parts.append(
            f"{self.request_count} request"
            if self.request_count == 1
            else f"{self.request_count} requests",
        )
        parts.append(_fmt_duration(self.duration_ms))
        return " · ".join(parts)

    def format_summary(self) -> str:
        """Compact one-liner. Prints after each response when the
        session has any data."""
        return f"session: {self._format_totals()}"

    def format_detailed(self) -> str:
        """``/usage`` view. Same totals plus per-request averages
        when there's been more than one request — single-request
        averages are just the request itself."""
        lines = [self.format_summary()]
        if self.request_count > 1:
            avg_in = self.input_tokens // self.request_count
            avg_out = self.output_tokens // self.request_count
            avg_cache = self.cache_read // self.request_count
            avg_cost = self.cost_usd / self.request_count
            avg_parts: list[str] = []
            if avg_in:
                avg_parts.append(f"{avg_in:,} in")
            if avg_out:
                avg_parts.append(f"{avg_out:,} out")
            if avg_cache:
                avg_parts.append(f"{avg_cache:,} cached")
            if avg_cost > 0.0:
                avg_parts.append(_fmt_cost(avg_cost))
            if avg_parts:
                lines.append("average: " + " · ".join(avg_parts) + " per request")
        return "\n".join(lines)


_WEEK_MS = 7 * 24 * 60 * 60 * 1000


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _metric_line(total: Dict[str, Any]) -> str:
    tokens = int(total.get("input_tokens") or 0) + int(total.get("output_tokens") or 0)
    count = int(total.get("count") or 0)
    return " · ".join([
        _fmt_cost(float(total.get("cost_usd") or 0.0)),
        f"{_fmt_tokens(tokens)} tokens",
        f"{count} request" if count == 1 else f"{count} requests",
    ])


def _top_breakdown(rows: List[Dict[str, Any]], limit: int = 5) -> str:
    shown = rows[:limit]
    parts = [f"{r.get('key') or '—'} {_fmt_cost(float(r.get('cost_usd') or 0.0))}" for r in shown]
    if len(rows) > limit:
        parts.append(f"(+{len(rows) - limit} more)")
    return " · ".join(parts)


def format_cost_overview() -> str:
    """All-time cost overview from the ledger for ``/usage`` — total, by agent,
    by model, and the last 7 days. Empty string when no cost has been recorded
    (so the session block stands alone). Spans all sessions, not just this one."""
    from app.io.cost_ledger import get_cost_ledger
    ledger = get_cost_ledger()
    agg = ledger.aggregate()
    total = agg["total"]
    if not int(total.get("count") or 0):
        return ""
    lines = ["all time: " + _metric_line(total)]
    if agg.get("by_agent"):
        lines.append("  by agent: " + _top_breakdown(agg["by_agent"]))
    if agg.get("by_model"):
        lines.append("  by model: " + _top_breakdown(agg["by_model"]))
    week = ledger.aggregate(since_ms=int(time.time() * 1000) - _WEEK_MS)["total"]
    if int(week.get("count") or 0):
        lines.append("last 7 days: " + _metric_line(week))
    return "\n".join(lines)
