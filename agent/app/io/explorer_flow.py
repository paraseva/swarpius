"""Roon API Explorer — dev-only WS handler for raw browse navigation.

Intentionally bypasses every Swarpius browse abstraction (RoonBrowseMixin,
BrowseSessionManager, category reconciler, fuzzy matcher, parallel-browse
patch). Its purpose is to surface the Roon browse API verbatim — duplicate
levels, hint variations, action lists and all — so the operator can
catalogue the patterns directly from a web UI.

Only ``api.browse_browse`` and ``api.browse_load`` are called. The result
is passed straight back to the frontend with no field stripping.

Gated by ``ENABLE_ROON_EXPLORER`` — the WS dispatcher in
``websocket_flow.py`` drops the channel entirely when the flag is off,
so this module is never imported in production use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_log = logging.getLogger("swarpius.explorer")

EXPLORER_SESSION_KEY = "explorer"
EXPLORER_HIERARCHY = "search"
LOAD_COUNT = 100


async def handle_explorer(
    payload: dict, runtime: Any, loop: asyncio.AbstractEventLoop,
) -> dict:
    """Dispatch one explorer action to the raw Roon browse API.

    Returns ``{ok: True, action, browse, load}`` on success. The
    surrounding ``_handle_json_request`` wrapper converts any raised
    exception into ``{ok: False, error: ...}``.
    """
    if runtime.roon_connection is None:
        raise RuntimeError("Roon connection not available")
    api = runtime.roon_connection.api

    base = {
        "hierarchy": EXPLORER_HIERARCHY,
        "multi_session_key": EXPLORER_SESSION_KEY,
    }

    action = payload.get("action")
    if action == "search":
        text = payload.get("input")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("search action requires non-empty 'input'")
        opts = {**base, "input": text, "pop_all": True}
    elif action == "navigate":
        item_key = payload.get("item_key")
        if not isinstance(item_key, str) or not item_key:
            raise ValueError("navigate action requires 'item_key'")
        opts = {**base, "item_key": item_key}
    elif action == "up":
        opts = {**base, "pop_levels": 1}
    else:
        raise ValueError(f"Unknown explorer action: {action!r}")

    load_opts = {**base, "offset": 0, "count": LOAD_COUNT}

    def _do() -> tuple[Any, Any]:
        _log.debug("explorer browse_browse opts=%s", opts)
        browse = api.browse_browse(opts)
        _log.debug("explorer browse_load opts=%s", load_opts)
        load = api.browse_load(load_opts)
        return browse, load

    browse, load = await loop.run_in_executor(None, _do)
    return {"ok": True, "action": action, "browse": browse, "load": load}
