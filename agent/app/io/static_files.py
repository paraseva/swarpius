"""Static-file serving for the bundled web client.

In a PyInstaller bundle the web client's ``dist/`` directory sits
alongside the agent and is served on the same port as the WebSocket.
In source mode (or Docker, where nginx serves the UI from a separate
container) the agent leaves static serving off when ``dist/`` is not
discoverable, so existing setups are unaffected.

``serve_dist()`` returns a library-neutral tuple so the HTTP
transport (currently ``websockets`` process_request) can wrap the
result without coupling the file-resolution logic to a specific
server library.
"""
from __future__ import annotations

import mimetypes
import sys
from pathlib import Path
from typing import Optional

# Paths ending in one of these extensions are treated as concrete
# static assets: if the file is missing we return 404 rather than
# falling back to index.html. Extension-less paths and unknown
# extensions are routed to index.html so client-side SPA routing
# keeps working.
_ASSET_EXTENSIONS = frozenset({
    ".html", ".htm", ".css", ".js", ".mjs", ".map",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".json", ".txt", ".xml", ".wasm",
})


def resolve_dist_dir() -> Optional[Path]:
    """Locate the bundled web client's ``dist`` directory.

    Search order:

    1. PyInstaller runtime — ``<_MEIPASS>/web-client/dist``.
    2. Source layout — ``<repo>/web-client/dist`` relative to this file.

    Returns ``None`` when neither exists, signalling that the caller
    should run WS-only (no static route registered).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "web-client" / "dist"
        if (bundled / "index.html").is_file():
            return bundled

    source = Path(__file__).resolve().parent.parent.parent / "web-client" / "dist"
    if (source / "index.html").is_file():
        return source

    return None


def serve_dist(
    dist_dir: Path,
    request_path: str,
) -> tuple[int, dict[str, str], bytes]:
    """Resolve ``request_path`` against ``dist_dir`` and build a response.

    Returns ``(status, headers, body)``. Transport-neutral so the
    websockets ``process_request`` shim and a future aiohttp handler
    can both consume it without touching the resolution logic.

    Behaviour:

    - ``/`` and the empty path serve ``index.html``.
    - A path that resolves to a file inside ``dist_dir`` serves that file.
    - A path with an unknown extension falls back to ``index.html`` (SPA).
    - A path with a known asset extension that doesn't exist → 404.
    - ``..`` segments that escape ``dist_dir`` → 404.
    """
    clean = request_path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if not clean:
        return _serve_file(dist_dir / "index.html")

    dist_root = dist_dir.resolve()
    try:
        target = (dist_dir / clean).resolve()
        target.relative_to(dist_root)
    except (ValueError, OSError):
        return _not_found()

    if target.is_file():
        return _serve_file(target)

    suffix = target.suffix.lower()
    if suffix not in _ASSET_EXTENSIONS:
        index = dist_dir / "index.html"
        if index.is_file():
            return _serve_file(index)

    return _not_found()


def _serve_file(path: Path) -> tuple[int, dict[str, str], bytes]:
    try:
        body = path.read_bytes()
    except OSError:
        return _not_found()
    mime, _ = mimetypes.guess_type(path.name)
    if mime is None:
        mime = "application/octet-stream"
    headers = {
        "Content-Type": mime,
        "Content-Length": str(len(body)),
    }
    return 200, headers, body


def _not_found() -> tuple[int, dict[str, str], bytes]:
    body = b"404 Not Found\n"
    return (
        404,
        {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Length": str(len(body)),
        },
        body,
    )
