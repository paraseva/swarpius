"""Concise console summary of boot validation for CLI mode.

The web UI renders validation status from the broadcast payload; CLI
users get this text equivalent so a degraded provider/backend is visible
and a slow probe doesn't look like a hang. Only the coordinator gates
startup, so sub-agent and backend failures render as *degraded*, not
fatal (a fatal coordinator failure is handled by the caller before this
summary runs).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SummaryItem:
    label: str
    detail: str


def degraded_items(status) -> List[SummaryItem]:
    """Enabled sub-agents and backends that failed — the non-essential
    failures worth flagging. The coordinator is excluded (its failure
    gates startup separately); disabled and passing rows are omitted."""
    items: List[SummaryItem] = []
    for r in status.results:
        if r.agent == "coordinator" or not r.enabled or r.ok is not False:
            continue
        items.append(SummaryItem(
            label=f"{r.agent} ({r.model or '—'})",
            detail=r.detail or r.error_kind or "",
        ))
    for b in status.backends:
        if b.ok:
            continue
        items.append(SummaryItem(
            label=b.label,
            detail=b.detail or b.error_kind or "",
        ))
    return items


def format_summary(status, elapsed: Optional[float] = None) -> str:
    """Rich-markup block: a header (ready / reduced capabilities) plus a
    line per checked item, ✓/✗ with the failure detail. ``elapsed`` (if
    given) is shown in the header."""
    took = f"  [dim]({elapsed:.1f}s)[/dim]" if elapsed is not None else ""
    if degraded_items(status):
        lines = [f"[yellow]⚠ Started with reduced capabilities[/yellow]{took}"]
    else:
        lines = [f"[green]✓ Providers & services ready[/green]{took}"]

    def _row(label: str, ok: Optional[bool], detail: str) -> str:
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        tail = "" if ok else f"  [dim]{detail}[/dim]"
        return f"   {mark} {label}{tail}"

    for r in status.results:
        if not r.enabled or r.ok is None:
            continue
        lines.append(_row(
            f"{r.agent} [dim]({r.model or '—'})[/dim]",
            r.ok,
            r.detail or r.error_kind or "",
        ))
    for b in status.backends:
        lines.append(_row(b.label, b.ok, b.detail or b.error_kind or ""))

    return "\n".join(lines)
