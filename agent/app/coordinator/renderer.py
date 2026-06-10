"""Base class for ``AgentEvent`` rendering subscribers.

``CliRenderer`` (terminal spinner / panels) and ``WsBroadcaster``
(WebSocket frames) are both event-bus subscribers that translate
``AgentEvent`` instances into mode-specific output. The shared base
class enforces one rule:

    every concrete renderer must declare a handler for every
    ``AgentEvent`` variant — silent drops mask wiring gaps.

Subclasses provide a ``_handlers`` mapping from event type to handler;
``handle`` looks up by ``type(event)`` and raises
``NotImplementedError`` if an event type isn't registered. An
explicit do-nothing handler is a valid registration — it documents
that this renderer deliberately ignores that event class.

``RequestLogger`` is a polymorphic subscriber (introspects every event
as a dataclass) and doesn't inherit from this base.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Type


def ignore_event(_event: Any) -> None:
    """Explicit-ignore handler for events a renderer deliberately
    drops. Use this in ``_handlers`` to document the intent —
    silently omitting a key from the registry now raises rather
    than dropping at runtime."""


class Renderer(ABC):
    @abstractmethod
    def _handlers(self) -> Dict[Type[Any], Callable[[Any], None]]:
        """Map of ``type(event) → handler``. Must cover every
        ``AgentEvent`` variant; use ``lambda _: None`` for events the
        renderer deliberately ignores."""

    def handle(self, event: Any) -> None:
        handler = self._handlers().get(type(event))
        if handler is None:
            raise NotImplementedError(
                f"{type(self).__name__} has no handler registered for "
                f"event type {type(event).__name__}",
            )
        handler(event)
