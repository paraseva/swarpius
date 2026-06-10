"""Time-window check for the CLI prompt's 2-tap Ctrl+C exit."""

from __future__ import annotations

from typing import Optional


def is_recent(
    timestamp: Optional[float],
    now: float,
    window_seconds: float = 2.0,
) -> bool:
    """``True`` iff ``timestamp`` is set and within ``window_seconds``
    of ``now``."""
    if timestamp is None:
        return False
    return (now - timestamp) <= window_seconds
