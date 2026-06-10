"""Parser for the ``TTS_URL`` setting.

F5-TTS speaks raw TCP on its socket-server port. The canonical form
is ``host:port``; any leading ``scheme://`` is stripped silently so
``tcp://``, ``http://``, etc. are all accepted as the same TCP
endpoint.
"""
from __future__ import annotations

import re
from typing import Tuple


class TtsUrlError(ValueError):
    """Raised when ``TTS_URL`` can't be parsed as a TCP endpoint."""


_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+\-.]*://", re.IGNORECASE)


def parse_tts_url(raw: str) -> Tuple[str, int]:
    """Parse a TTS_URL value into ``(host, port)``.

    Strips any leading ``scheme://`` and parses the remainder as
    ``host:port``. Raises ``TtsUrlError`` if the host or port is
    missing or out of range.
    """
    if not raw:
        raise TtsUrlError("TTS_URL is empty")
    value = _SCHEME_RE.sub("", raw.strip())
    if not value:
        raise TtsUrlError("TTS_URL is empty")

    if ":" not in value:
        raise TtsUrlError(
            f"TTS_URL {raw!r} is missing the port — expected 'host:port'.",
        )
    host, _, port_str = value.rpartition(":")
    host = host.strip()
    port_str = port_str.strip()
    if not host:
        raise TtsUrlError(f"TTS_URL {raw!r} is missing the host.")
    try:
        port = int(port_str)
    except ValueError as exc:
        raise TtsUrlError(
            f"TTS_URL {raw!r} port must be a number, got {port_str!r}.",
        ) from exc
    if not 1 <= port <= 65535:
        raise TtsUrlError(
            f"TTS_URL {raw!r} port {port} is out of range (1-65535).",
        )
    return host, port
