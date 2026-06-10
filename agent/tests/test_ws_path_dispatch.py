"""Path-based dispatch for incoming WebSocket connections.

Two paths share the agent's WS port:
- ``/ws`` — chat / settings / Roon control (the existing handler)
- ``/tts`` — TTS proxy to the F5-TTS server

The handler inspects the upgrade-request path and routes accordingly.
The static-files HTTP handler must let both paths fall through to
the WS upgrade rather than treating ``/tts`` as a missing asset.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


class TestHttpHandlerLetsTtsPathThrough(unittest.TestCase):
    """``_make_http_handler``'s callback returns ``None`` for paths
    that should WS-upgrade. ``/tts`` must be in that set or the
    static-files layer will 404 the connection before it ever
    reaches the WS handler."""

    def _handler_for(self, path: str):
        from agent import _make_http_handler

        # dist_dir value is irrelevant for these tests — the WS/TTS
        # branches short-circuit before touching it.
        handler = _make_http_handler(dist_dir=MagicMock())
        request = MagicMock()
        request.path = path
        return handler(connection=MagicMock(), request=request)

    def test_ws_path_falls_through(self):
        self.assertIsNone(self._handler_for("/ws"))

    def test_ws_path_with_query_falls_through(self):
        self.assertIsNone(self._handler_for("/ws?session=abc"))

    def test_tts_path_falls_through(self):
        self.assertIsNone(self._handler_for("/tts"))

    def test_tts_path_with_query_falls_through(self):
        self.assertIsNone(self._handler_for("/tts?session=abc"))

    def test_non_ws_non_tts_path_invokes_static_serving(self):
        """Static-file paths must NOT short-circuit — otherwise the
        web client would never be served. We verify the static-file
        helper is reached; what it returns is its own contract."""
        from agent import _make_http_handler

        with patch("agent.serve_dist") as serve:
            serve.return_value = (200, {}, b"")
            handler = _make_http_handler(dist_dir=MagicMock())
            request = MagicMock()
            request.path = "/index.html"
            handler(connection=MagicMock(), request=request)
            self.assertEqual(serve.call_count, 1)


class TestWebSocketHandlerDispatches(unittest.TestCase):
    """The WS handler inspects ``request.path`` and routes:
    ``/tts`` → ``tts_proxy.handle``; ``/ws`` → normal chat flow."""

    def _fake_ws(self, path: str):
        ws = MagicMock()
        ws.request = MagicMock()
        ws.request.path = path
        ws.remote_address = ("127.0.0.1", 12345)
        return ws

    def test_tts_path_routes_to_proxy(self):
        """A WS connection on ``/tts`` is handled by the TTS proxy
        and bypasses the chat handler's session-registration and
        runtime-init machinery entirely."""
        import asyncio

        from app.io.websocket_flow import websocket_handler

        ws = self._fake_ws("/tts")
        proxy_calls = []

        async def fake_proxy_handle(websocket):
            proxy_calls.append(websocket)

        runtime = MagicMock()
        with patch("tts.proxy.handle", new=fake_proxy_handle):
            asyncio.new_event_loop().run_until_complete(
                websocket_handler(
                    websocket=ws,
                    runtime=runtime,
                    ws_clients=set(),
                    process_request_fn=lambda *_, **__: None,
                    arbitrate_interrupt_fn=lambda *_: None,
                    ws_send_fn=lambda *_: None,
                ),
            )

        # Proxy got the connection; chat path didn't run
        self.assertEqual(len(proxy_calls), 1)
        self.assertIs(proxy_calls[0], ws)
        # Chat-only side effects shouldn't have fired
        runtime.ensure_initialised.assert_not_called()


if __name__ == "__main__":
    unittest.main()
