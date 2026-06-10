"""Two-tap Ctrl+C cancellation for ``swarpius.py`` (CLI mode).

Wraps a request in a daemon worker thread so the main thread is free
to field ``KeyboardInterrupt``. First Ctrl+C signals graceful
cancellation via the ``threading.Event`` passed to the target; second
Ctrl+C tells the caller to break out of its loop.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, Tuple


class CancelHandler:
    """Two-tap state machine driven by ``KeyboardInterrupt``.

    The first call to :meth:`handle_interrupt` sets the underlying
    ``threading.Event`` and fires ``on_first``; the second fires
    ``on_second`` and signals the caller to stop waiting (returns
    ``True``). Further calls keep returning ``True`` so a stuck
    worker can't trap the user.
    """

    def __init__(
        self,
        cancel_event: threading.Event,
        on_first: Optional[Callable[[], None]] = None,
        on_second: Optional[Callable[[], None]] = None,
    ) -> None:
        self._cancel_event = cancel_event
        self._on_first = on_first
        self._on_second = on_second
        self._fired = False

    def handle_interrupt(self) -> bool:
        if not self._fired:
            self._cancel_event.set()
            self._fired = True
            if self._on_first is not None:
                self._on_first()
            return False
        if self._on_second is not None:
            self._on_second()
        return True


def run_request_with_cancel(
    target: Callable[[threading.Event], Any],
    *,
    on_first_interrupt: Optional[Callable[[], None]] = None,
    on_second_interrupt: Optional[Callable[[], None]] = None,
    poll_interval: float = 0.1,
) -> Tuple[bool, Optional[BaseException]]:
    """Run ``target(cancel_event)`` in a daemon thread and field
    ``KeyboardInterrupt`` on the main thread via :class:`CancelHandler`.

    Returns ``(exit_requested, exception_or_None)``.

    ``exit_requested=True`` means the user pressed Ctrl+C twice and
    the caller should exit its loop. The daemon worker may still be
    alive in that case; it dies with the process.
    """
    cancel_event = threading.Event()
    captured: list[BaseException] = []

    def _wrapper() -> None:
        try:
            target(cancel_event)
        except BaseException as exc:  # noqa: BLE001 -- captured + returned for main-thread handling; thread-local KeyboardInterrupt/SystemExit would otherwise be lost
            captured.append(exc)

    worker = threading.Thread(target=_wrapper, daemon=True)
    worker.start()

    handler = CancelHandler(
        cancel_event,
        on_first=on_first_interrupt,
        on_second=on_second_interrupt,
    )

    # Wrap the whole wait phase so a KeyboardInterrupt landing
    # between ``join`` returning and the next ``is_alive`` check is
    # still caught.
    while True:
        try:
            while worker.is_alive():
                worker.join(timeout=poll_interval)
            return False, captured[0] if captured else None
        except KeyboardInterrupt:
            if handler.handle_interrupt():
                return True, captured[0] if captured else None
