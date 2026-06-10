"""Format the per-request token / cost / duration summary line that
follows the agent's response in CLI mode.

Mirrors the data the web client's RequestSummaryPanel renders, but
collapsed onto one line to keep the CLI feeling lightweight.
"""

from __future__ import annotations

from typing import Any, Dict


def _fmt_duration(duration_ms: int) -> str:
    if duration_ms < 1000:
        return f"{int(duration_ms)}ms"
    return f"{duration_ms / 1000:.1f}s"


def _fmt_cost(cost_usd: float) -> str:
    """Render a cost value. Below ``$0.0001`` print a floor marker
    so a non-zero charge isn't silently lost to rounding."""
    if cost_usd >= 0.0001:
        return f"${cost_usd:.4f}"
    return "<$0.0001"


def format_usage_summary(
    usage: Dict[str, Any],
    *,
    steps: int,
    duration_ms: int,
) -> str:
    """Compact one-liner. Fields with zero/missing values are
    suppressed — a non-cached, free run reads as
    ``"100 in · 20 out · 1 step · 200ms"`` rather than padding
    every absent metric with zeros."""
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cost_usd = float(usage.get("cost_usd") or 0.0)

    parts: list[str] = []
    if input_tokens:
        parts.append(f"{input_tokens:,} in")
    if output_tokens:
        parts.append(f"{output_tokens:,} out")
    if cache_read:
        parts.append(f"{cache_read:,} cached")
    if cost_usd > 0.0:
        parts.append(_fmt_cost(cost_usd))

    parts.append(f"{steps} step" if steps == 1 else f"{steps} steps")
    parts.append(_fmt_duration(duration_ms))
    return " · ".join(parts)
