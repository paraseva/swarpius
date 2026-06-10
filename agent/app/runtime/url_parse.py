from __future__ import annotations

from urllib.parse import urlparse


def parse_host_port(
    url_value: str,
    default_host: str = "localhost",
    default_port: int = 9998,
) -> tuple[str, int]:
    candidate = (url_value or "").strip()
    if not candidate:
        return default_host, default_port
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    return parsed.hostname or default_host, parsed.port or default_port
