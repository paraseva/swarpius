"""Small time-formatting helpers shared across context providers.

Lives at the ``app/`` root so it can be imported from any subpackage
without crossing layering boundaries (e.g. ``app/roon/`` importing
from ``app/coordinator/`` and vice versa).
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_log = logging.getLogger("swarpius.time")


@lru_cache(maxsize=8)
def _resolve_zone(name: str) -> Optional[ZoneInfo]:
    """Resolve an IANA name to a ZoneInfo, or None (system local) if invalid.
    Cached so an invalid name is warned about once, not on every call."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        _log.warning(
            "Invalid SWARPIUS_TIMEZONE %r — falling back to system local time.",
            name,
        )
        return None


def get_local_timezone() -> Optional[ZoneInfo]:
    """The timezone Swarpius uses for all timestamps (``SWARPIUS_TIMEZONE``), or
    None to use the system local zone. None preserves the legacy
    naive-``datetime`` behaviour (correct for source/installer); a value forces
    that zone even when the process clock is UTC (e.g. a Docker container)."""
    from app.settings import get_settings

    name = get_settings().time_zone
    return _resolve_zone(name) if name else None


def local_now() -> datetime:
    """Current wall-clock time in the configured local zone, as a *naive*
    datetime. Naive (not zone-aware) keeps it directly comparable to the naive
    timestamps stored across the codebase — no aware/naive clashes — while still
    reflecting the configured zone."""
    return datetime.now(get_local_timezone()).replace(tzinfo=None)


def local_strftime(timestamp: float, fmt: str) -> str:
    """Format *timestamp* (epoch seconds) in the configured local zone."""
    return datetime.fromtimestamp(timestamp, get_local_timezone()).strftime(fmt)


def local_day(timestamp: float) -> str:
    """The ``YYYY-MM-DD`` calendar day of *timestamp* in the configured local
    zone — the date used to group conversations and name log directories."""
    return local_strftime(timestamp, "%Y-%m-%d")


def local_today() -> str:
    """Today's ``YYYY-MM-DD`` in the configured local zone."""
    return local_now().strftime("%Y-%m-%d")


def local_timezone_label() -> str:
    """Human-readable description of the resolved local zone, for startup
    diagnostics. The IANA name when ``SWARPIUS_TIMEZONE`` is set, otherwise the
    system zone tagged ``(system default)`` — so a container silently left on
    UTC reads as ``UTC (system default)`` and the misconfiguration is obvious."""
    zone = get_local_timezone()
    if zone is not None:
        return str(zone)
    return f"{datetime.now().astimezone().tzname()} (system default)"


def format_relative_time(seconds_ago: float) -> str:
    """Format an elapsed duration as a compact "N ago" phrase.

    Bucket boundaries: under a minute → "just now"; under an hour →
    "N min ago"; under a day → "N hr ago"; otherwise "N day(s) ago".
    Negative inputs (which can arise from clock jitter) are clamped
    to zero so the output is always non-negative.
    """
    seconds_ago = max(0.0, seconds_ago)
    if seconds_ago < 60:
        return "just now"
    if seconds_ago < 3600:
        return f"{int(seconds_ago // 60)} min ago"
    if seconds_ago < 86400:
        return f"{int(seconds_ago // 3600)} hr ago"
    days = int(seconds_ago // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"
