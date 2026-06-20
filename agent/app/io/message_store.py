"""Persistent message store for WebSocket message replay across browser refreshes.

Stores outbound WS messages in SQLite so they can be replayed when a new
client connects.  Cleared on server startup by default; use ``--keep-history``
to retain messages from a previous session (shown greyed-out in the frontend).

Uses an abstract ``MessageStore`` interface with two implementations
exercised today: :class:`SqliteMessageStore` for WS mode and
:class:`NullMessageStore` for CLI mode and tests.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.io.state_db import StateDb


class MessageStore(ABC):
    """Abstract interface for WS message persistence."""

    @abstractmethod
    def clear(self) -> None:
        """Delete all stored messages."""

    @abstractmethod
    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None) -> None:
        """Store a single outbound WS message."""

    @abstractmethod
    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve stored messages in order. Each dict has channel,
        payload, meta, created_at. When ``since_ms`` is supplied, only
        messages with ``created_at >= since_ms`` are returned."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""


class SqliteMessageStore(MessageStore):
    """SQLite-backed implementation over the shared :class:`StateDb`.

    Does not own the connection — ``StateDb`` does — so transcript writes
    share a connection (and lock) with the persisted state, letting a
    request's transcript + state snapshot commit in one transaction.
    """

    def __init__(self, state_db: StateDb) -> None:
        self._db = state_db

    def clear(self) -> None:
        with self._db.lock:
            self._db.conn.execute("DELETE FROM ws_messages")
            self._db.conn.commit()

    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None) -> None:
        payload_json = json.dumps(payload, default=str)
        meta_json = json.dumps(meta, default=str) if meta else None
        now_ms = int(time.time() * 1000)
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO ws_messages (channel, payload, meta, created_at) VALUES (?, ?, ?, ?)",
                (channel, payload_json, meta_json, now_ms),
            )
            self._db.conn.commit()

    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._db.lock:
            if since_ms is None:
                cursor = self._db.conn.execute(
                    "SELECT channel, payload, meta, created_at FROM ws_messages ORDER BY id",
                )
            else:
                cursor = self._db.conn.execute(
                    "SELECT channel, payload, meta, created_at FROM ws_messages "
                    "WHERE created_at >= ? ORDER BY id",
                    (since_ms,),
                )
            rows = cursor.fetchall()
        result = []
        for channel, payload_json, meta_json, created_at in rows:
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                payload = payload_json
            meta = None
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except (json.JSONDecodeError, TypeError):
                    # Stored meta is opt-in legacy JSON; leave as None on
                    # parse failure rather than poison the whole row.
                    pass
            result.append({"channel": channel, "payload": payload, "meta": meta, "created_at": created_at})
        return result

    def close(self) -> None:
        # The connection belongs to StateDb; its owner closes it.
        pass


class NullMessageStore(MessageStore):
    """Discard-only store for tests and CLI mode."""

    def clear(self) -> None:
        pass

    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None) -> None:
        pass

    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        return []

    def close(self) -> None:
        pass


# Module-level singleton — callers use get/set, never import the instance directly
_store: MessageStore = NullMessageStore()


def get_message_store() -> MessageStore:
    return _store


def set_message_store(store: MessageStore) -> None:
    global _store  # noqa: PLW0603
    _store = store
