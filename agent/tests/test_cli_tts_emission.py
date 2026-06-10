"""Pin the CLI-mode TTS gating contract.

``AppIO.sayit`` should:

- do nothing in WS mode (frontend handles TTS itself);
- do nothing when TTS_URL is unset / empty / malformed;
- do nothing when the agent is not in chat_panel_agents;
- spawn a TTS thread when TTS_URL is set AND mode is CLI AND
  the agent is in chat_panel_agents.

The host + port reach the speak coroutine via ``parse_tts_url``.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


def _make_app_io(run_mode: str = "cli"):
    from app.io import AppIO

    speak_calls: list[tuple] = []

    async def _capture_speak(text, server_ip="", server_port=0):
        speak_calls.append((text, server_ip, server_port))

    bridge = AppIO(
        run_mode_getter=lambda: run_mode,
        console_getter=lambda: None,
        speak_text_coro=_capture_speak,
        ws_clients=set(),
        get_ws_event_loop=lambda: None,
    )
    return bridge, speak_calls


class TestSayitGating(unittest.TestCase):

    def test_ws_mode_short_circuits_no_thread_no_speak(self):
        bridge, speak_calls = _make_app_io(run_mode="ws")
        with patch.dict(os.environ, {"TTS_URL": "localhost:9998"}, clear=False):
            bridge.sayit("Coordinator", "hello", chat_panel_agents={"Coordinator"})
        self.assertIsNone(bridge._tts_thread)
        self.assertEqual(speak_calls, [])

    def test_unset_tts_url_skips_tts(self):
        bridge, speak_calls = _make_app_io(run_mode="cli")
        env_no_tts = {k: v for k, v in os.environ.items() if k != "TTS_URL"}
        with patch.dict(os.environ, env_no_tts, clear=True):
            bridge.sayit("Coordinator", "hello", chat_panel_agents={"Coordinator"})
        self.assertIsNone(bridge._tts_thread)

    def test_malformed_tts_url_skips_tts(self):
        """A URL the parser rejects (e.g. missing port) shouldn't
        crash sayit — TTS just silently doesn't fire."""
        bridge, speak_calls = _make_app_io(run_mode="cli")
        with patch.dict(os.environ, {"TTS_URL": "localhost"}, clear=False):
            bridge.sayit("Coordinator", "hello", chat_panel_agents={"Coordinator"})
        self.assertIsNone(bridge._tts_thread)

    def test_agent_not_in_chat_panel_skips_tts(self):
        bridge, speak_calls = _make_app_io(run_mode="cli")
        with patch.dict(os.environ, {"TTS_URL": "localhost:9998"}, clear=False):
            bridge.sayit("Diagnostic", "internal", chat_panel_agents={"Coordinator"})
        self.assertIsNone(bridge._tts_thread)

    def test_tts_url_set_spawns_thread_with_parsed_host_and_port(self):
        bridge, speak_calls = _make_app_io(run_mode="cli")
        with patch.dict(os.environ, {"TTS_URL": "192.168.1.50:9998"}, clear=False):
            bridge.sayit("Coordinator", "hello", chat_panel_agents={"Coordinator"})
            self.assertIsNotNone(bridge._tts_thread)
            bridge._tts_thread.join(timeout=2.0)
        self.assertEqual(len(speak_calls), 1)
        text, ip, port = speak_calls[0]
        self.assertEqual(text, "hello")
        self.assertEqual(ip, "192.168.1.50")
        self.assertEqual(port, 9998)

    def test_tcp_scheme_alias_works(self):
        bridge, speak_calls = _make_app_io(run_mode="cli")
        with patch.dict(os.environ, {"TTS_URL": "tcp://localhost:9998"}, clear=False):
            bridge.sayit("Coordinator", "hello", chat_panel_agents={"Coordinator"})
            self.assertIsNotNone(bridge._tts_thread)
            bridge._tts_thread.join(timeout=2.0)
        self.assertEqual(speak_calls[0][1], "localhost")
        self.assertEqual(speak_calls[0][2], 9998)


if __name__ == "__main__":
    unittest.main()
