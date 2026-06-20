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
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.io.db_schema import open_versioned_db


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
    """SQLite-backed implementation."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        # Opens with WAL, runs schema migrations, and moves a corrupt /
        # future DB aside rather than failing startup. The schema (incl.
        # ws_messages) lives in db_schema.py, the single source of truth.
        self._conn = open_versioned_db(self._db_path)
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ws_messages")
            self._conn.commit()

    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None) -> None:
        payload_json = json.dumps(payload, default=str)
        meta_json = json.dumps(meta, default=str) if meta else None
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "INSERT INTO ws_messages (channel, payload, meta, created_at) VALUES (?, ?, ?, ?)",
                (channel, payload_json, meta_json, now_ms),
            )
            self._conn.commit()

    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if since_ms is None:
                cursor = self._conn.execute(
                    "SELECT channel, payload, meta, created_at FROM ws_messages ORDER BY id",
                )
            else:
                cursor = self._conn.execute(
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
        with self._lock:
            self._conn.close()


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
