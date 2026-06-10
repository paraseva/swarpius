"""Background reachability poller for pollable backends.

Generalises the old TTS-only poller: every ``pollable`` backend in the
registry (SearXNG, TTS) is re-probed on a timer so the Settings UI
auto-recovers when a service comes back. Brave/Tavily are not pollable (a
real probe would spend a query), so they're excluded. The probe must run
server-side — e.g. the ``/tts`` WS proxy accepts the browser regardless
of the TTS server's health, so a client-side probe always reports green.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

_PROBE_INTERVAL_SECONDS = 30


def start_backend_health_loop(
    stop_event: threading.Event,
    *,
    on_change: Optional[Callable[[], None]] = None,
    interval_seconds: int = _PROBE_INTERVAL_SECONDS,
) -> threading.Thread:
    """Spawn the periodic backend reachability poll on a daemon thread.
    ``on_change`` fires only when a probe's ``ok`` flag transitions."""
    thread = threading.Thread(
        target=_run_loop,
        name="swarpius-backend-health",
        kwargs={
            "stop_event": stop_event,
            "on_change": on_change,
            "interval_seconds": max(5, interval_seconds),
        },
        daemon=True,
    )
    thread.start()
    return thread


def _poll_once(
    settings,
    validator,
    *,
    on_change: Optional[Callable[[], None]] = None,
) -> None:
    """Probe every pollable backend once and persist any status change."""
    from app.settings.backends import active_backend_checks

    for check in active_backend_checks(settings):
        if not check.pollable:
            continue
        result = check.status_probe(settings)
        if validator.update_backend(result) and on_change is not None:
            try:
                on_change()
            except Exception:
                log.exception("backend health on_change callback failed")


def _run_loop(
    stop_event: threading.Event,
    *,
    on_change: Optional[Callable[[], None]],
    interval_seconds: int,
) -> None:
    from app.settings import get_settings
    from app.settings.validation import get_validator

    while not stop_event.is_set():
        try:
            _poll_once(get_settings(), get_validator(), on_change=on_change)
        except Exception:
            log.exception("Unexpected error in backend health poll — continuing")

        if stop_event.wait(timeout=interval_seconds):
            return
