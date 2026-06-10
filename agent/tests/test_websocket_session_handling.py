"""Tests for single-session hygiene on the WebSocket handler.

Only one active socket is allowed at a time. A new connection with the
same ``session_id`` (browser reconnect) silently replaces the slot
without closing the old socket — closing it would race the client's
own close handler and trigger a reconnect cascade. A different
``session_id`` (another tab/device) displaces with
``CLOSE_CODE_SESSION_TAKEOVER`` so the displaced client can show a
"taken over" overlay and stop auto-reconnecting.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Optional

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

import app.io.websocket_flow as wsflow  # noqa: E402
from app.constants import CLOSE_CODE_SESSION_TAKEOVER  # noqa: E402


@dataclass
class _FakeRequest:
    path: str = ""


class _FakeSocket:
    """Mock websocket with just the bits ``websocket_flow`` touches."""

    def __init__(self, path: str = "") -> None:
        self.request = _FakeRequest(path=path)
        self.closed_with: Optional[tuple[int, str]] = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


class TestExtractSessionId(unittest.TestCase):

    def test_returns_session_id_from_query_string(self):
        ws = _FakeSocket(path="/?session_id=abc-123")
        self.assertEqual(wsflow._extract_session_id(ws), "abc-123")

    def test_returns_none_when_query_missing(self):
        ws = _FakeSocket(path="/")
        self.assertIsNone(wsflow._extract_session_id(ws))

    def test_returns_none_when_other_params_only(self):
        ws = _FakeSocket(path="/?foo=bar")
        self.assertIsNone(wsflow._extract_session_id(ws))

    def test_handles_missing_request_attr(self):
        class Bare:
            pass
        self.assertIsNone(wsflow._extract_session_id(Bare()))  # type: ignore[arg-type]


class TestRegisterSession(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        wsflow._active_session = None

    async def asyncTearDown(self) -> None:
        wsflow._active_session = None

    async def test_registers_first_connection_without_closing_anything(self):
        ws = _FakeSocket()
        returned = await wsflow._register_session(ws, "abc")
        self.assertEqual(returned, "abc")
        self.assertEqual(wsflow._active_session, ("abc", ws))
        self.assertIsNone(ws.closed_with)

    async def test_same_session_id_does_not_close_old_socket(self):
        """Browser reconnect scenario — same session_id arrives on a new
        socket. The slot is replaced silently, leaving the old socket
        to clean up via its own close path. Closing it here would
        cascade: the client's close handler would schedule a reconnect,
        which would displace and close again, and we'd loop."""
        old = _FakeSocket()
        await wsflow._register_session(old, "session-A")

        new = _FakeSocket()
        await wsflow._register_session(new, "session-A")

        self.assertIsNone(old.closed_with)
        self.assertEqual(wsflow._active_session, ("session-A", new))

    async def test_different_session_id_closes_old_with_takeover_code(self):
        """Another tab/device scenario — different session_id displaces
        the current one. Old client sees the takeover code and should
        stop auto-reconnecting."""
        old = _FakeSocket()
        await wsflow._register_session(old, "session-A")

        new = _FakeSocket()
        await wsflow._register_session(new, "session-B")

        self.assertIsNotNone(old.closed_with)
        code, _reason = old.closed_with
        self.assertEqual(code, CLOSE_CODE_SESSION_TAKEOVER)
        self.assertEqual(wsflow._active_session, ("session-B", new))

    async def test_generates_anon_id_when_missing(self):
        """CLI/curl clients don't supply session_id; server generates
        one so the handler still has a consistent identity for
        logging/disambiguation."""
        ws = _FakeSocket()
        returned = await wsflow._register_session(ws, None)
        self.assertTrue(returned.startswith("anon-"))
        self.assertEqual(wsflow._active_session, (returned, ws))


class TestClearSessionIfCurrent(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        wsflow._active_session = None

    async def asyncTearDown(self) -> None:
        wsflow._active_session = None

    async def test_clears_slot_when_socket_matches(self):
        ws = _FakeSocket()
        await wsflow._register_session(ws, "abc")
        await wsflow._clear_session_if_current(ws)
        self.assertIsNone(wsflow._active_session)

    async def test_does_not_clear_when_displaced_socket_cleans_up(self):
        """A takeover-displaced socket's finally block must not evict
        the replacement that took its slot. Without this guard the new
        tab would lose its registration the moment the old tab finished
        its cleanup."""
        old = _FakeSocket()
        await wsflow._register_session(old, "A")
        new = _FakeSocket()
        await wsflow._register_session(new, "B")

        # The old socket reaches its finally block after being displaced:
        await wsflow._clear_session_if_current(old)

        # New socket still holds the slot.
        self.assertEqual(wsflow._active_session, ("B", new))


if __name__ == "__main__":
    unittest.main()
