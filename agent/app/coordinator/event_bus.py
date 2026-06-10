"""Event bus for AgentEvent delivery.

Subscribers register a callable; the bus invokes each in subscribe
order on every ``emit``. Synchronous delivery — the bus returns when
every handler has returned.

A misbehaving subscriber must never break the request flow, so handler
exceptions are caught, logged, and swallowed. Subscribers that need
strict failure semantics should track their own state and raise at a
safer point.
"""

from __future__ import annotations

import logging
from typing import Callable, List

from app.coordinator.events import AgentEvent

_log = logging.getLogger("swarpius.event_bus")

EventHandler = Callable[[AgentEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: List[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def emit(self, event: AgentEvent) -> None:
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                _log.exception("Event subscriber raised; continuing")
