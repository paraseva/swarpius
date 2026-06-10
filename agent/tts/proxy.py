"""WebSocket-to-TCP TTS proxy hosted inside the agent.

Bridges a browser WebSocket connection to the F5-TTS socket server.
For every text frame the browser sends, the proxy opens a fresh TCP
connection to the TTS server configured via ``TTS_URL``, forwards
the text bytes, then streams the response back as binary WebSocket
frames. The TCP server appends ``b"END"`` to its final chunk — the
proxy strips that marker and emits a ``"END"`` text frame to the
browser so the client knows the utterance is complete.

Error reporting is a single ``"ERROR"`` text frame so the browser
has a clear failure signal without leaking server internals.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from app.settings import get_settings
from tts.url import TtsUrlError, parse_tts_url

log = logging.getLogger(__name__)

_TCP_READ_CHUNK = 8192
_TCP_CONNECT_TIMEOUT_SECONDS = 5.0
_END_MARKER = b"END"


class _WebSocketLike(Protocol):
    """Minimal surface area the proxy uses from a websockets
    ServerConnection — defined as a Protocol so test fakes can
    satisfy it without inheritance."""

    async def send(self, data: Any) -> None: ...
    def __aiter__(self) -> Any: ...


async def handle(websocket: _WebSocketLike) -> None:
    """Run the proxy for one browser WebSocket connection.

    Iterates the WS for text frames, forwards each to F5-TTS, and
    streams back the synthesised audio. Returns when the browser
    disconnects or after a fatal error has been signalled.
    """
    settings = get_settings()
    try:
        host, port = parse_tts_url(settings.tts_url or "")
    except TtsUrlError as exc:
        log.warning("TTS proxy rejecting connection: %s", exc)
        await _try_send(websocket, "ERROR")
        return

    try:
        async for message in websocket:
            if not isinstance(message, str):
                continue
            text = message.strip()
            if not text:
                continue
            await _forward_one(host, port, text, websocket)
    except Exception:  # noqa: BLE001 — surface as ERROR, don't crash the WS handler
        log.exception("TTS proxy handler error")
        await _try_send(websocket, "ERROR")


async def _forward_one(
    host: str, port: int, text: str, websocket: _WebSocketLike,
) -> None:
    """One TCP round-trip — open, send text, stream response, close."""
    try:
        # happy_eyeballs_delay=0.0 races v4 against v6 immediately
        # instead of trying v6 first and waiting for it to time out.
        # A hostname like ``localhost`` resolves to both ``::1`` and
        # ``127.0.0.1``; when F5-TTS only binds v4 and the host
        # silently drops v6 (rather than returning ECONNREFUSED),
        # ordered behaviour stalls for seconds before falling back.
        # The RFC-default 0.25s is calibrated to prefer v6 on the
        # open internet — we don't care which family wins for a
        # local TTS server, and 250ms is a meaningful fraction of
        # F5-TTS's ~300ms generation time.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, happy_eyeballs_delay=0.0),
            timeout=_TCP_CONNECT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.info(
            "TTS server timed out after %.0fs at %s:%s",
            _TCP_CONNECT_TIMEOUT_SECONDS, host, port,
        )
        await _try_send(websocket, "ERROR")
        return
    except OSError as exc:
        log.info("TTS server unreachable at %s:%s — %s", host, port, exc)
        await _try_send(websocket, "ERROR")
        return

    try:
        writer.write(text.encode("utf-8"))
        await writer.drain()
        while True:
            data = await reader.read(_TCP_READ_CHUNK)
            if not data:
                break
            if data.endswith(_END_MARKER):
                audio = data[: -len(_END_MARKER)]
                if audio:
                    await websocket.send(audio)
                await websocket.send("END")
                return
            await websocket.send(data)
    except Exception:  # noqa: BLE001
        log.exception("TTS proxy stream error")
        await _try_send(websocket, "ERROR")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def _try_send(websocket: _WebSocketLike, payload: Any) -> None:
    """Best-effort send — swallow errors so a closed WS doesn't
    promote a status signal into a handler crash."""
    try:
        await websocket.send(payload)
    except Exception:  # noqa: BLE001
        pass
