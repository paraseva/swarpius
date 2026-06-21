"""Age-based pruning of the persistent history in the shared state DB.

Chat and diagnostics share the ``ws_messages`` table (distinguished by
channel) but have independent retention windows — diagnostics are bulkier
and shorter-lived than the chat transcript. Listening history has its own
window. A window of 0 (or less) keeps that store forever.

Run on startup so the on-disk history stays bounded without an extra
background loop.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

_log = logging.getLogger("app.io.history_retention")

_DAY_MS = 24 * 60 * 60 * 1000


def prune_history(
    state_db: Any,
    *,
    chat_days: int,
    diagnostics_days: int,
    listening_days: int,
    now_ms: int,
) -> Dict[str, int]:
    """Delete rows older than each window. Returns rows-deleted per store."""
    deleted = {"chat": 0, "diagnostics": 0, "listening": 0}
    with state_db.transaction() as conn:
        if chat_days and chat_days > 0:
            cur = conn.execute(
                "DELETE FROM ws_messages WHERE channel = 'chat' AND created_at < ?",
                (now_ms - chat_days * _DAY_MS,),
            )
            deleted["chat"] = cur.rowcount
        if diagnostics_days and diagnostics_days > 0:
            cur = conn.execute(
                "DELETE FROM ws_messages WHERE channel != 'chat' AND created_at < ?",
                (now_ms - diagnostics_days * _DAY_MS,),
            )
            deleted["diagnostics"] = cur.rowcount
        if listening_days and listening_days > 0:
            cur = conn.execute(
                "DELETE FROM listening_history WHERE ts < ?",
                (now_ms - listening_days * _DAY_MS,),
            )
            deleted["listening"] = cur.rowcount
    if any(deleted.values()):
        _log.info(
            "Pruned history: %d chat, %d diagnostics, %d listening rows",
            deleted["chat"], deleted["diagnostics"], deleted["listening"],
        )
    return deleted
