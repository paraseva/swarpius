"""Renderer-to-event-bus contract.

The EventBus emits ``AgentEvent`` instances; ``CliRenderer`` and
``WsBroadcaster`` are the two rendering subscribers. The contract is
that every renderer makes an explicit choice for every event type —
silent drops mask wiring gaps (an event that nobody renders is invisible
to its target surface, with no warning).

These tests pin the contract behaviourally:

* an event type the renderer doesn't know about raises
  ``NotImplementedError`` rather than disappearing silently;
* every variant of the ``AgentEvent`` union is acknowledged (no
  ``NotImplementedError`` for any known event type).

Together they imply: every known event has an explicit handler, and
every unknown event is loud about being unknown.
"""

from __future__ import annotations

import unittest
from typing import Any, Union, get_args, get_origin, get_type_hints
from unittest.mock import MagicMock

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.cli.renderer import CliRenderer  # noqa: E402
from app.coordinator.events import AgentEvent  # noqa: E402
from app.io.ws_broadcaster import WsBroadcaster  # noqa: E402


def _placeholder(typ: Any) -> Any:
    """Return a type-appropriate placeholder for dataclass field
    instantiation. Used only to build minimal event instances so the
    completeness test can iterate every AgentEvent variant without
    knowing each one's exact field shape."""
    origin = get_origin(typ)
    if origin is Union:
        return None
    if origin in (list, tuple, dict, set):
        return origin()
    if typ is int:
        return 0
    if typ is str:
        return ""
    if typ is bool:
        return False
    if typ is dict:
        return {}
    if typ is tuple:
        return ()
    if typ is Any:
        return None
    return None


def _minimal_event(event_cls: type) -> Any:
    hints = get_type_hints(event_cls)
    return event_cls(**{name: _placeholder(typ) for name, typ in hints.items()})


def _all_event_classes() -> tuple:
    return get_args(AgentEvent)


class _UnknownEvent:
    """Stand-in for "an event type the renderer wasn't told about".
    Not a registered AgentEvent variant — used to verify the renderer
    is loud about unknown events rather than silently dropping them."""


class TestCliRendererCoversAgentEvents(unittest.TestCase):
    def _renderer(self) -> CliRenderer:
        return CliRenderer(rich_console=MagicMock())

    def test_unknown_event_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._renderer().handle(_UnknownEvent())

    def test_every_known_event_dispatches_without_raising(self) -> None:
        r = self._renderer()
        for event_cls in _all_event_classes():
            with self.subTest(event=event_cls.__name__):
                r.handle(_minimal_event(event_cls))


class TestWsBroadcasterCoversAgentEvents(unittest.TestCase):
    def _broadcaster(self) -> WsBroadcaster:
        # The broadcaster touches runtime.tool_registry / usage_tracker
        # to build payloads; MagicMock answers any attribute access
        # with another MagicMock, which is enough to keep dispatch from
        # raising. The point of the test is NotImplementedError, not
        # the resulting payload shape (that's pinned by test_ws_emissions).
        return WsBroadcaster(ws_send_fn=MagicMock(), runtime=MagicMock())

    def test_unknown_event_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._broadcaster().handle(_UnknownEvent())

    def test_every_known_event_dispatches_without_raising(self) -> None:
        b = self._broadcaster()
        for event_cls in _all_event_classes():
            with self.subTest(event=event_cls.__name__):
                b.handle(_minimal_event(event_cls))


if __name__ == "__main__":
    unittest.main()
