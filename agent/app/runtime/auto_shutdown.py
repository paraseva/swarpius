"""Bundle-mode auto-shutdown when the browser disconnects.

The installer launches the agent like a native app: double-click the
exe, default browser opens to the UI, close the tab → the user
expects the process to exit. This module wires that behaviour:

- 60 s startup grace: if no client connects after launch, quit. Covers
  the case where the auto-opened browser tab is blocked or never
  reaches the WS.
- 2 s disconnect grace: rides out an F5 / brief blip, then the process
  exits immediately. No countdown — the bundle has no visible console, so
  one would only hold the port and leave the user unsure whether the
  server had quit (blocking a relaunch).

Source / Docker / WSL keep their long-running behaviour — this code
stays inactive unless ``_running_from_bundle()`` returns True.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)


_DISCONNECT_GRACE_SECONDS = 2.0
_STARTUP_GRACE_SECONDS = 60.0


class AutoShutdown:
    """Single-instance helper. Methods are safe to call from the WS
    event loop; the timer handles run there too."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        signal_shutdown: Callable[[], None],
        *,
        disconnect_grace_seconds: float = _DISCONNECT_GRACE_SECONDS,
        startup_grace_seconds: float = _STARTUP_GRACE_SECONDS,
    ) -> None:
        self._loop = loop
        self._signal_shutdown = signal_shutdown
        self._disconnect_grace = disconnect_grace_seconds
        self._startup_grace = startup_grace_seconds
        self._count = 0
        self._pending: Optional[asyncio.TimerHandle] = None

    def start_startup_grace(self) -> None:
        """Begin the post-boot countdown. Cancelled on the first client
        connect; fires ``signal_shutdown`` if no one arrives."""
        self._schedule(self._startup_grace, self._fire, reason="startup grace")

    def on_connect(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        self._count += 1

    def on_disconnect(self) -> None:
        if self._count == 0:
            # Spurious — never connected. Ignoring keeps the trigger
            # tied to a real N→0 transition.
            return
        self._count -= 1
        if self._count == 0:
            self._schedule(
                self._disconnect_grace, self._fire, reason="disconnect grace",
            )

    def _schedule(
        self, delay: float, callback: Callable[[], None], *, reason: str,
    ) -> None:
        if self._pending is not None:
            self._pending.cancel()
        log.info("Auto-shutdown armed (%s, %.1fs)", reason, delay)
        self._pending = self._loop.call_later(delay, callback)

    def _fire(self) -> None:
        """Shut down unless a client reconnected during the grace window — or
        a restart is in flight, where the browser dropped because the server
        is closing to respawn. Firing then would exit 0 (no respawn) and turn
        the restart into a quit."""
        self._pending = None
        if self._count > 0:
            log.debug("Auto-shutdown timer fired with live clients — ignoring")
            return
        from app.runtime.restart_signal import is_restart_requested
        if is_restart_requested():
            log.info("Auto-shutdown stood down — restart in progress")
            return
        log.info("Auto-shutdown firing — no clients within grace window")
        self._signal_shutdown()
