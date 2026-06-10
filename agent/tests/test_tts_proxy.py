"""Behavioural tests for the WS↔TCP TTS proxy.

The proxy bridges a browser WebSocket connection to the F5-TTS
socket server. For each text message the browser sends, the proxy
opens a fresh TCP connection to the F5-TTS server, forwards the text, then
streams response bytes back as binary WS frames. The TCP server
appends ``b"END"`` to signal completion; the proxy translates that
into a ``"END"`` text frame to the browser.

Tests use a real localhost TCP server (not mocks) so the actual
asyncio stream machinery is on the call path — that's where socket-
level defects hide.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any, List, Optional
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


class _FakeWebSocket:
    """Stand-in for ``websockets`` ServerConnection.

    Async-iterates over queued messages, captures everything the
    proxy sends. Tests construct it with the messages the browser
    would send and then inspect ``sent`` afterwards.
    """

    def __init__(self, messages: List[Any]) -> None:
        self._messages = list(messages)
        self.sent: List[Any] = []
        self.closed = False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for m in self._messages:
            yield m

    async def send(self, data: Any) -> None:
        if self.closed:
            raise ConnectionError("ws closed")
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


def _start_tcp_server(
    on_request,
    *,
    host: str = "127.0.0.1",
) -> "tuple[asyncio.AbstractServer, int]":
    """Bind an asyncio TCP server on an ephemeral port and return
    (server, port). ``on_request(reader, writer, text_bytes)`` is
    awaited for every client connection — the test scripts what
    the TCP server behaviour looks like."""
    server: Optional[asyncio.AbstractServer] = None

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        text = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            text += chunk
            # Real F5-TTS reads until the client stops sending; for
            # tests we treat the first chunk as the request body.
            await on_request(reader, writer, text)
            return

    async def _make():
        nonlocal server
        server = await asyncio.start_server(handler, host, 0)
        return server.sockets[0].getsockname()[1]

    loop = asyncio.get_event_loop()
    port = loop.run_until_complete(_make())
    assert server is not None
    return server, port


class _Loop:
    """Context manager that gives each test its own event loop —
    needed because we spawn a TCP server bound to a port that lives
    for the test's lifetime."""

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        return self.loop

    def __exit__(self, *_):
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.close()
        asyncio.set_event_loop(None)


class TestHappyPath(unittest.TestCase):

    def test_text_message_streams_audio_back_with_end_frame(self):
        """The proxy: opens TCP, sends the browser text, streams
        chunks back as binary frames, and emits 'END' string frame
        when the TCP server sends the END marker."""
        async def tcp_server(reader, writer, text):
            self.assertEqual(text, b"hello world")
            writer.write(b"\x01\x02\x03")
            writer.write(b"\x04\x05END")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                # Test fixture teardown — close races are fine.
                pass

        with _Loop() as loop:
            server, port = _start_tcp_server(tcp_server)
            try:
                from tts import proxy as tts_proxy

                ws = _FakeWebSocket(["hello world"])
                with patch.dict(
                    os.environ,
                    {"TTS_URL": f"127.0.0.1:{port}"},
                    clear=False,
                ):
                    from app.settings import reset_settings_for_tests
                    reset_settings_for_tests()
                    loop.run_until_complete(tts_proxy.handle(ws))

                self.assertEqual(ws.sent[-1], "END")
                # All non-END frames are binary; concatenated they form
                # the audio stream the browser will play.
                audio = b"".join(s for s in ws.sent if isinstance(s, (bytes, bytearray)))
                self.assertEqual(audio, b"\x01\x02\x03\x04\x05")
            finally:
                server.close()
                loop.run_until_complete(server.wait_closed())

    def test_audio_only_in_terminal_frame(self):
        """TCP server that sends only one chunk ending in END still
        gets the audio prefix forwarded before the END string."""
        async def tcp_server(reader, writer, text):
            writer.write(b"\xaa\xbbEND")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                # Test fixture teardown — close races are fine.
                pass

        with _Loop() as loop:
            server, port = _start_tcp_server(tcp_server)
            try:
                from app.settings import reset_settings_for_tests
                from tts import proxy as tts_proxy

                ws = _FakeWebSocket(["one"])
                with patch.dict(os.environ, {"TTS_URL": f"127.0.0.1:{port}"}, clear=False):
                    reset_settings_for_tests()
                    loop.run_until_complete(tts_proxy.handle(ws))

                self.assertEqual(ws.sent, [b"\xaa\xbb", "END"])
            finally:
                server.close()
                loop.run_until_complete(server.wait_closed())


class TestErrorPaths(unittest.TestCase):

    def test_tcp_server_refused_emits_error_frame(self):
        """When the TCP connect to the TTS server fails (no server listening
        on the configured port), the proxy sends an 'ERROR' string
        frame so the browser can surface a useful message."""
        # Reserve a port by binding then immediately closing — gives
        # us a port number that's near-certainly unbound.
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        unbound_port = s.getsockname()[1]
        s.close()

        with _Loop() as loop:
            from app.settings import reset_settings_for_tests
            from tts import proxy as tts_proxy

            ws = _FakeWebSocket(["hi"])
            with patch.dict(
                os.environ,
                {"TTS_URL": f"127.0.0.1:{unbound_port}"},
                clear=False,
            ):
                reset_settings_for_tests()
                loop.run_until_complete(tts_proxy.handle(ws))

            self.assertEqual(ws.sent, ["ERROR"])

    def test_unconfigured_tts_url_emits_error_frame_without_connecting(self):
        """If TTS_URL isn't set at all (or is malformed), the proxy
        shouldn't crash — it should close the WS cleanly after
        signalling ERROR so the browser sees the problem."""
        with _Loop() as loop:
            from app.settings import reset_settings_for_tests
            from tts import proxy as tts_proxy

            ws = _FakeWebSocket(["hi"])
            with patch.dict(os.environ, {}, clear=True):
                reset_settings_for_tests()
                loop.run_until_complete(tts_proxy.handle(ws))

            self.assertEqual(ws.sent, ["ERROR"])

    def test_malformed_tts_url_emits_error_frame(self):
        with _Loop() as loop:
            from app.settings import reset_settings_for_tests
            from tts import proxy as tts_proxy

            ws = _FakeWebSocket(["hi"])
            with patch.dict(
                os.environ,
                {"TTS_URL": "localhost"},
                clear=False,
            ):
                reset_settings_for_tests()
                loop.run_until_complete(tts_proxy.handle(ws))

            self.assertEqual(ws.sent, ["ERROR"])


class TestMessageFiltering(unittest.TestCase):

    def test_binary_messages_from_ws_are_ignored(self):
        """The browser is expected to send text frames; if it sends
        a binary frame the proxy silently drops it (no TCP server
        connect, no audio response)."""
        tcp_server_hit = []

        async def tcp_server(reader, writer, text):
            tcp_server_hit.append(text)
            writer.write(b"END")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                # Test fixture teardown — close races are fine.
                pass

        with _Loop() as loop:
            server, port = _start_tcp_server(tcp_server)
            try:
                from app.settings import reset_settings_for_tests
                from tts import proxy as tts_proxy

                ws = _FakeWebSocket([b"binary-junk"])
                with patch.dict(os.environ, {"TTS_URL": f"127.0.0.1:{port}"}, clear=False):
                    reset_settings_for_tests()
                    loop.run_until_complete(tts_proxy.handle(ws))

                self.assertEqual(tcp_server_hit, [])
                self.assertEqual(ws.sent, [])
            finally:
                server.close()
                loop.run_until_complete(server.wait_closed())

    def test_empty_text_messages_are_ignored(self):
        tcp_server_hit = []

        async def tcp_server(reader, writer, text):
            tcp_server_hit.append(text)
            writer.write(b"END")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                # Test fixture teardown — close races are fine.
                pass

        with _Loop() as loop:
            server, port = _start_tcp_server(tcp_server)
            try:
                from app.settings import reset_settings_for_tests
                from tts import proxy as tts_proxy

                ws = _FakeWebSocket(["   ", ""])
                with patch.dict(os.environ, {"TTS_URL": f"127.0.0.1:{port}"}, clear=False):
                    reset_settings_for_tests()
                    loop.run_until_complete(tts_proxy.handle(ws))

                self.assertEqual(tcp_server_hit, [])
                self.assertEqual(ws.sent, [])
            finally:
                server.close()
                loop.run_until_complete(server.wait_closed())


class TestMultipleRoundTrips(unittest.TestCase):

    def test_two_consecutive_messages_each_get_fresh_tcp_connection(self):
        """The proxy opens a new TCP connection per message — long-
        lived browser sessions sending multiple TTS requests over
        the same WS shouldn't reuse a closed TCP server."""
        hits: List[bytes] = []

        async def tcp_server(reader, writer, text):
            hits.append(text)
            writer.write(f"chunk-{len(hits)}".encode())
            writer.write(b"END")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                # Test fixture teardown — close races are fine.
                pass

        with _Loop() as loop:
            server, port = _start_tcp_server(tcp_server)
            try:
                from app.settings import reset_settings_for_tests
                from tts import proxy as tts_proxy

                ws = _FakeWebSocket(["first", "second"])
                with patch.dict(os.environ, {"TTS_URL": f"127.0.0.1:{port}"}, clear=False):
                    reset_settings_for_tests()
                    loop.run_until_complete(tts_proxy.handle(ws))

                self.assertEqual(hits, [b"first", b"second"])
                # Two audio frames + two END frames, in order
                kinds = ["bin" if isinstance(s, (bytes, bytearray)) else s for s in ws.sent]
                self.assertEqual(kinds, ["bin", "END", "bin", "END"])
            finally:
                server.close()
                loop.run_until_complete(server.wait_closed())


if __name__ == "__main__":
    unittest.main()
