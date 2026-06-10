"""Roon Core connection-health watcher.

The agent↔Roon-Core link is independent of the agent↔browser WebSocket:
the Core can drop mid-session while the agent stays up. The roonapi
library reconnects on its own (its `_socket_watcher` rebuilds the socket
after ~20s), but nothing surfaces the outage to the UI, so zone cards
silently go stale.

This watcher polls the connection state and emits a `roon-core-status`
event on each transition so the UI can show/clear a "reconnecting"
overlay. We poll rather than hook the socket's `on_close` because that
event is unreliable for abrupt drops (a killed Core / network partition
surfaces as a ping-timeout, not a clean close) — `is_connected` is the
same signal the library itself polls.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger("swarpius.roon_health")

_POLL_INTERVAL_SECONDS = 2


class RoonHealthMonitor:
    """Tracks Roon Core connection transitions.

    ``poll`` returns ``"lost"`` / ``"connected"`` on an edge and ``None``
    otherwise. The first poll records the baseline silently so a healthy
    startup doesn't fire a spurious event.
    """

    def __init__(self) -> None:
        self._last: Optional[bool] = None

    def poll(self, is_connected: bool) -> Optional[str]:
        if self._last is None or is_connected == self._last:
            self._last = is_connected
            return None
        self._last = is_connected
        return "connected" if is_connected else "lost"


def format_roon_status_message(state: str) -> str:
    """Rich-markup console line for a Core status transition (CLI mode,
    where there's no modal to show)."""
    if state == "lost":
        return "[bold red]Lost connection to Roon Core — reconnecting…[/bold red]"
    return "[bold green]Roon Core reconnected.[/bold green]"


def start_roon_health_loop(
    stop_event: threading.Event,
    *,
    is_connected: Callable[[], bool],
    emit: Callable[[str], None],
    interval_seconds: int = _POLL_INTERVAL_SECONDS,
) -> threading.Thread:
    """Spawn the Core-health poll on a daemon thread.

    ``is_connected`` reads the current link state; ``emit`` is called with
    ``"lost"`` / ``"connected"`` on each transition.
    """
    thread = threading.Thread(
        target=_run_loop,
        name="swarpius-roon-health",
        kwargs={
            "stop_event": stop_event,
            "is_connected": is_connected,
            "emit": emit,
            "interval_seconds": max(1, interval_seconds),
        },
        daemon=True,
    )
    thread.start()
    return thread


def _run_loop(
    stop_event: threading.Event,
    *,
    is_connected: Callable[[], bool],
    emit: Callable[[str], None],
    interval_seconds: int,
) -> None:
    monitor = RoonHealthMonitor()
    while not stop_event.is_set():
        try:
            event = monitor.poll(is_connected())
            if event is not None:
                emit(event)
        except Exception:
            log.exception("Roon health poll failed")
        if stop_event.wait(timeout=interval_seconds):
            return
