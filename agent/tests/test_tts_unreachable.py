"""``tts.speak_text`` must degrade gracefully when the TTS server
is unreachable or the local audio stack can't open a device.

Pre-fix, ``client_socket.connect()`` ran outside the try/except so a
``ConnectionRefusedError`` (server down) raised straight out of the
worker thread, dumping a traceback above the CLI prompt. The connect
also had no timeout, so a half-up server could block indefinitely.

CLI-only contract (WS mode does not go through ``speak_text`` —
``AppIO.sayit`` returns early when run_mode == "ws"):

  * Failures are caught, never re-raised — the user gets a silent
    fallback rather than a tear-down.
  * The first failure emits one WARNING containing the phrase
    "TTS service unavailable" so the user knows what happened.
  * After the first failure the CLI TTS path is **disabled** for the
    rest of the process: subsequent ``speak_text`` calls return
    immediately and never touch the socket or the audio stack. This
    keeps repeated chat responses from spamming the same warning and
    from re-triggering PortAudio's ALSA / JACK C-side noise on WSL.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import unittest
from unittest.mock import patch

from tts import tts as tts_module


class _FakeSocket:
    """Drop-in for ``socket.socket(...)`` that raises on connect."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.closed = False

    def settimeout(self, _seconds: float) -> None:
        pass

    def connect(self, _addr) -> None:
        raise self._exc

    def close(self) -> None:
        self.closed = True


class TestUnreachableServer(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the CLI-disabled flag and notice callback between tests
        # so each case starts from "TTS enabled, never tried yet, no
        # callback registered".
        tts_module._cli_tts_disabled = False
        tts_module.set_notice_callback(None)

    def test_connection_refused_does_not_raise(self) -> None:
        with patch.object(
            tts_module, "_create_socket",
            return_value=_FakeSocket(ConnectionRefusedError(111, "refused")),
        ):
            asyncio.run(tts_module.speak_text(
                "hello", server_ip="127.0.0.1", server_port=9998,
            ))

    def test_first_failure_warns_once_and_disables(self) -> None:
        with patch.object(
            tts_module, "_create_socket",
            return_value=_FakeSocket(ConnectionRefusedError(111, "refused")),
        ):
            with self.assertLogs("tts.tts", level="WARNING") as cm:
                asyncio.run(tts_module.speak_text(
                    "hello", server_ip="127.0.0.1", server_port=9998,
                ))
        self.assertEqual(len(cm.records), 1)
        message = cm.records[0].getMessage()
        self.assertIn("TTS service unavailable", message)
        self.assertIn("127.0.0.1:9998", message)
        self.assertTrue(
            tts_module._cli_tts_disabled,
            "first failure must flip the CLI-disabled flag",
        )

    def test_disabled_short_circuits_without_touching_socket(self) -> None:
        # Once disabled, speak_text must not even attempt to open a
        # socket — the whole point is to avoid repeating the failed
        # connect (and the PortAudio C-side noise on the audio path).
        tts_module._cli_tts_disabled = True

        def _should_not_be_called() -> None:
            raise AssertionError(
                "speak_text touched the socket after CLI TTS was disabled",
            )

        with (
            patch.object(tts_module, "_create_socket", side_effect=_should_not_be_called),
            patch.object(tts_module.logger, "warning") as warn_mock,
        ):
            asyncio.run(tts_module.speak_text(
                "hello", server_ip="127.0.0.1", server_port=9998,
            ))
        warn_mock.assert_not_called()

    def test_notice_callback_receives_message_instead_of_logger(self) -> None:
        # When the CLI registers a notice callback, the disable
        # message goes there (so it can be rendered via Rich) instead
        # of through ``logger.warning`` — which in CLI mode would
        # dump the verbose stderr WARNING format on the user.
        received: list[str] = []
        tts_module.set_notice_callback(received.append)

        with (
            patch.object(
                tts_module, "_create_socket",
                return_value=_FakeSocket(ConnectionRefusedError(111, "refused")),
            ),
            patch.object(tts_module.logger, "warning") as warn_mock,
        ):
            asyncio.run(tts_module.speak_text(
                "hello", server_ip="127.0.0.1", server_port=9998,
            ))

        self.assertEqual(len(received), 1)
        self.assertIn("TTS service unavailable", received[0])
        warn_mock.assert_not_called()

    def test_timeout_error_caught(self) -> None:
        """Half-up server (TCP connect hangs) — wrapped as
        socket.timeout."""
        with patch.object(
            tts_module, "_create_socket",
            return_value=_FakeSocket(socket.timeout("timed out")),
        ):
            asyncio.run(tts_module.speak_text(
                "hello", server_ip="127.0.0.1", server_port=9998,
            ))
        # No exception escapes — that's the contract.


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
