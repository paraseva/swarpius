"""Conversation day-grouping must use the configured local timezone
(``SWARPIUS_TIMEZONE``), not the process clock.

The process can run in UTC (e.g. a Docker container) while the user is
elsewhere; without an explicit zone, a request just after local midnight is
bucketed into the previous UTC day and the per-day ``cNN`` counter never
resets. ``day_str`` is the primitive the day-boundary roll compares, so pinning
it to the configured zone pins the grouping behaviour above it.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app.runtime.conversation_tracker import day_str


@contextmanager
def _timezone(name: str):
    """Set ``SWARPIUS_TIMEZONE`` and drop the locked settings cache so the
    snapshot reflects it."""
    from app.settings.core import reset_settings_for_tests

    with patch.dict(os.environ, {"SWARPIUS_TIMEZONE": name}, clear=False):
        reset_settings_for_tests()
        try:
            yield
        finally:
            reset_settings_for_tests()


def test_day_str_uses_configured_timezone_not_process_clock():
    # 2026-06-24 23:30 UTC lands on different calendar days by zone, so this is
    # independent of the machine the test runs on:
    #   Asia/Tokyo (UTC+9)         -> 2026-06-25
    #   America/Los_Angeles (-7)   -> 2026-06-24
    ts = datetime(2026, 6, 24, 23, 30, tzinfo=timezone.utc).timestamp()

    with _timezone("Asia/Tokyo"):
        assert day_str(ts) == "2026-06-25"

    with _timezone("America/Los_Angeles"):
        assert day_str(ts) == "2026-06-24"


def test_listening_history_when_is_formatted_in_configured_timezone():
    # Listening-history times are formatted server-side and reported by the
    # assistant, so they must render in the configured local zone, not the
    # process clock (which is UTC in a Docker container).
    from tools.listening_history import _format_when

    ts_ms = int(datetime(2026, 6, 24, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)

    with _timezone("Asia/Tokyo"):  # UTC+9 -> 08:30 the next day
        assert _format_when(ts_ms) == "2026-06-25 08:30"

    with _timezone("America/Los_Angeles"):  # UTC-7 -> 16:30 the same day
        assert _format_when(ts_ms) == "2026-06-24 16:30"
