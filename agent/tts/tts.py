import asyncio
import contextlib
import logging
import os
import socket
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import pyaudio
except ImportError:
    np = None  # type: ignore[assignment]
    pyaudio = None  # type: ignore[assignment]

# How long to wait for the TTS server's TCP accept before declaring
# it unreachable. The CLI has just printed a chat response and
# expects spoken output to follow within seconds — a longer wait
# would leave the user staring at a stalled prompt.
CONNECT_TIMEOUT_S = 3.0

# Set on first failure (connect refused, audio device missing, etc.).
# WS mode does not go through ``speak_text`` — ``AppIO.sayit`` returns
# early when run_mode == "ws" — so this flag is CLI-only by
# construction and never affects the browser-side ``/tts`` proxy.
# Once disabled, subsequent calls return immediately so we don't spam
# the same warning or re-trigger PortAudio's ALSA / JACK C-side noise
# on machines without a working audio device (WSL, headless).
_cli_tts_disabled: bool = False

# CLI registers a Rich-aware callback so the disable notice renders
# as a coloured one-liner instead of the verbose stderr WARNING
# format. When unset (tests, headless harnesses), ``_disable_cli_tts``
# falls back to ``logger.warning`` so the message is still recorded.
_notice_callback: Optional[Callable[[str], None]] = None


def set_notice_callback(callback: Optional[Callable[[str], None]]) -> None:
    """Register a user-facing notice channel for CLI disable events.

    The callback receives the full pre-formatted message string and is
    responsible for any display + log-file write it wants. Passing
    ``None`` reverts to the logger fallback.
    """
    global _notice_callback
    _notice_callback = callback


def _disable_cli_tts(reason: str) -> None:
    """Flip the CLI-disabled flag and emit the one user-facing notice.

    Idempotent — subsequent callers find the flag already set and
    return without re-emitting.
    """
    global _cli_tts_disabled
    if _cli_tts_disabled:
        return
    _cli_tts_disabled = True
    message = (
        f"TTS service unavailable ({reason}); CLI speech disabled for this session."
    )
    if _notice_callback is not None:
        _notice_callback(message)
    else:
        logger.warning("%s", message)


@contextlib.contextmanager
def _swallow_stderr():
    """Redirect file descriptor 2 to /dev/null for the duration of
    the context. PortAudio writes ALSA / JACK probe errors directly
    via ``fprintf(stderr, ...)`` from C, so Python's ``logging``
    can't capture them — only an fd-level redirect keeps the user's
    terminal clean on WSL / headless boxes. Used narrowly around the
    ``PyAudio()`` open so genuine Python-side errors still surface.
    """
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)


def _create_socket() -> socket.socket:
    """Indirection so tests can substitute a fake without
    monkey-patching ``socket.socket`` globally — asyncio uses the
    same factory internally and breaks under a global patch."""
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


async def speak_text(text, server_ip="localhost", server_port=9998):
    if _cli_tts_disabled:
        return

    if pyaudio is None:
        _disable_cli_tts(
            "PyAudio not installed — install agent/requirements.txt + the "
            "portaudio system library for CLI speech",
        )
        return

    target = (server_ip, int(server_port))
    client_socket = _create_socket()
    client_socket.settimeout(CONNECT_TIMEOUT_S)

    try:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, client_socket.connect, target,
            )
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            _disable_cli_tts(
                f"cannot reach {server_ip}:{server_port} ({type(exc).__name__})",
            )
            return
        # Reset to blocking mode for the streaming reads below.
        client_socket.settimeout(None)

        first_chunk_time = None

        async def play_audio_stream():
            nonlocal first_chunk_time
            # Wrap PyAudio init in the fd-2 redirect — PortAudio dumps
            # ALSA / JACK probe errors to stderr from C before
            # ``p.open(...)`` ever returns or raises.
            with _swallow_stderr():
                p = pyaudio.PyAudio()
                stream = p.open(
                    format=pyaudio.paFloat32, channels=1, rate=24000,
                    output=True, frames_per_buffer=2048,
                )

            try:
                while True:
                    data = await asyncio.get_event_loop().run_in_executor(None, client_socket.recv, 8192)
                    if not data:
                        break
                    if data.endswith(b"END"):
                        break

                    audio_array = np.frombuffer(data, dtype=np.float32)
                    stream.write(audio_array.tobytes())

                    if first_chunk_time is None:
                        first_chunk_time = time.time()

            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()

        try:
            data_to_send = f"{text}".encode("utf-8")
            await asyncio.get_event_loop().run_in_executor(None, client_socket.sendall, data_to_send)
            await play_audio_stream()
        except Exception as e:
            _disable_cli_tts(f"audio playback error: {e}")

    finally:
        client_socket.close()


if __name__ == "__main__":
    text_to_send = "This is a test of the F5-TTS system. Hello, world!"

    asyncio.run(speak_text(text_to_send))
