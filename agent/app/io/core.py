from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable, Optional

from websockets.asyncio.server import ServerConnection

from app.io.message_store import get_message_store

_PERSIST_CHANNELS = frozenset({
    "chat", "agent-outputs", "tool-outputs",
    "llm-diagnostics", "usage-metrics", "errors",
})


class AppIO:
    def __init__(
        self,
        run_mode_getter: Callable[[], str],
        console_getter: Callable[[], Any],
        speak_text_coro: Callable[..., Any],
        ws_clients: set[ServerConnection],
        get_ws_event_loop: Callable[[], Optional[asyncio.AbstractEventLoop]],
    ) -> None:
        self._run_mode_getter = run_mode_getter
        self._console_getter = console_getter
        self._speak_text_coro = speak_text_coro
        self._ws_clients = ws_clients
        self._get_ws_event_loop = get_ws_event_loop
        self._tts_thread: Optional[threading.Thread] = None

    def ws_broadcast(self, message: dict[str, Any]) -> None:
        ws_event_loop = self._get_ws_event_loop()
        if self._run_mode_getter() != "ws" or ws_event_loop is None or not self._ws_clients:
            return

        payload = json.dumps(message)

        async def _send_all() -> None:
            dead: list[ServerConnection] = []
            for ws in list(self._ws_clients):
                try:
                    await ws.send(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_clients.discard(ws)

        try:
            asyncio.run_coroutine_threadsafe(_send_all(), ws_event_loop)
        except RuntimeError:
            # Loop is shut down — we're tearing down, nothing to broadcast.
            pass

    def ws_send(self, channel: str, payload: Any, meta: Optional[dict[str, Any]] = None) -> None:
        if channel in _PERSIST_CHANNELS:
            get_message_store().append(channel, payload, meta)
        message: dict[str, Any] = {"channel": channel, "payload": payload}
        if meta:
            message["meta"] = meta
        self.ws_broadcast(message)

    def sayit(
        self,
        agent_name: str,
        message: str,
        chat_panel_agents: set[str],
    ) -> None:
        if self._run_mode_getter() == "ws":
            return
        from app.settings import get_settings
        from tts.url import TtsUrlError, parse_tts_url
        tts_url = get_settings().tts_url or ""
        if not tts_url or agent_name not in chat_panel_agents:
            return
        try:
            tts_server_ip, tts_server_port = parse_tts_url(tts_url)
        except TtsUrlError:
            return

        def tts_func() -> None:
            asyncio.run(
                self._speak_text_coro(message, server_ip=tts_server_ip, server_port=tts_server_port),
            )

        if self._tts_thread:
            self._tts_thread.join()
        # daemon=True so a TTS thread stuck mid-stream (e.g. server
        # paused mid-utterance) doesn't prevent process exit.
        self._tts_thread = threading.Thread(target=tts_func, daemon=True)
        self._tts_thread.start()
