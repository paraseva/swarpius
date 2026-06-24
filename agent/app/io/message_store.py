"""Persistent message store for WebSocket message replay across browser refreshes.

Stores outbound WS messages in SQLite so they can be replayed when a new
client connects. History persists across restarts (pruned only by the
configured retention window), so the chat survives a server restart.

Uses an abstract ``MessageStore`` interface with two implementations
exercised today: :class:`SqliteMessageStore` for WS mode and
:class:`NullMessageStore` for CLI mode and tests.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.io.state_db import StateDb


def _local_day_bounds(ts_ms: int) -> tuple[int, int]:
    """Local-time [start, end) epoch-ms bounds of the calendar day containing
    ``ts_ms``."""
    start = datetime.fromtimestamp(ts_ms / 1000).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


class MessageStore(ABC):
    """Abstract interface for WS message persistence."""

    @abstractmethod
    def clear(self) -> None:
        """Delete all stored messages."""

    @abstractmethod
    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None,
               created_at: Optional[int] = None) -> None:
        """Store a single outbound WS message. ``created_at`` (epoch ms)
        overrides the default of now — used to stamp a message with its real
        event time rather than its (later) commit time."""

    @abstractmethod
    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve stored messages in order. Each dict has id, channel,
        payload, meta, created_at. When ``since_ms`` is supplied, only
        messages with ``created_at >= since_ms`` are returned."""

    @abstractmethod
    def load_day(self, before_ms: int, channel: Optional[str] = None) -> Dict[str, Any]:
        """Return the most recent non-empty calendar day at or before
        ``before_ms``: {"messages": [...], "has_older": bool}. With ``channel``,
        the day is found and loaded for that channel alone (so a sparse panel
        finds its own previous day of content, independently of other channels)."""

    @abstractmethod
    def load_range(self, start_ms: int, end_ms: int, channel: Optional[str] = None) -> Dict[str, Any]:
        """Return every message in ``[start_ms, end_ms)`` (oldest first):
        {"messages": [...], "has_older": bool}. With ``channel``, scoped to that
        channel alone. Used to fill the gap when
        jumping to an older date, keeping the loaded history contiguous."""

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

    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None,
               created_at: Optional[int] = None) -> None:
        payload_json = json.dumps(payload, default=str)
        meta_json = json.dumps(meta, default=str) if meta else None
        ts_ms = created_at if created_at is not None else int(time.time() * 1000)
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO ws_messages (channel, payload, meta, created_at) VALUES (?, ?, ?, ?)",
                (channel, payload_json, meta_json, ts_ms),
            )
            self._db.conn.commit()

    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._db.lock:
            if since_ms is None:
                cursor = self._db.conn.execute(
                    "SELECT id, channel, payload, meta, created_at FROM ws_messages ORDER BY id",
                )
            else:
                cursor = self._db.conn.execute(
                    "SELECT id, channel, payload, meta, created_at FROM ws_messages "
                    "WHERE created_at >= ? ORDER BY id",
                    (since_ms,),
                )
            rows = cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    def load_day(self, before_ms: int, channel: Optional[str] = None) -> Dict[str, Any]:
        """The lazy-load primitive: the most recent non-empty calendar day at
        or before ``before_ms`` (skipping empty days), with ``has_older`` set
        when earlier history exists. ``messages`` is empty when there is no
        history at or before the cursor. Day boundaries are local time
        (matching the date picker's native date input). With ``channel``, the
        find/load/has_older are all scoped to that channel."""
        # Optional channel filter applied identically to the find, load, and
        # has_older queries so a per-channel load is self-consistent.
        ch = " AND channel = ?" if channel else ""
        ch_arg = (channel,) if channel else ()
        with self._db.lock:
            row = self._db.conn.execute(
                f"SELECT MAX(created_at) FROM ws_messages WHERE created_at <= ?{ch}",
                (before_ms, *ch_arg),
            ).fetchone()
            newest = row[0] if row else None
            if newest is None:
                return {"messages": [], "has_older": False}
            day_start, day_end = _local_day_bounds(newest)
            rows = self._db.conn.execute(
                f"SELECT id, channel, payload, meta, created_at FROM ws_messages "
                f"WHERE created_at >= ? AND created_at < ?{ch} ORDER BY id",
                (day_start, day_end, *ch_arg),
            ).fetchall()
            has_older = bool(
                self._db.conn.execute(
                    f"SELECT EXISTS(SELECT 1 FROM ws_messages WHERE created_at < ?{ch})",
                    (day_start, *ch_arg),
                ).fetchone()[0],
            )
            assignments = self._assignment_map(day_start, day_end) if channel == "chat" else {}
        messages = [self._row_to_dict(r) for r in rows]
        self._stamp_request_ids(messages, assignments)
        return {
            "messages": messages,
            "has_older": has_older,
        }

    def load_range(self, start_ms: int, end_ms: int, channel: Optional[str] = None) -> Dict[str, Any]:
        # Optional channel filter applied identically to the load and has_older
        # queries, so a per-channel range stays self-consistent (mirrors load_day).
        ch = " AND channel = ?" if channel else ""
        ch_arg = (channel,) if channel else ()
        with self._db.lock:
            rows = self._db.conn.execute(
                f"SELECT id, channel, payload, meta, created_at FROM ws_messages "
                f"WHERE created_at >= ? AND created_at < ?{ch} ORDER BY created_at, id",
                (start_ms, end_ms, *ch_arg),
            ).fetchall()
            has_older = bool(
                self._db.conn.execute(
                    f"SELECT EXISTS(SELECT 1 FROM ws_messages WHERE created_at < ?{ch})",
                    (start_ms, *ch_arg),
                ).fetchone()[0],
            )
            assignments = self._assignment_map(start_ms, end_ms) if channel == "chat" else {}
        messages = [self._row_to_dict(r) for r in rows]
        self._stamp_request_ids(messages, assignments)
        return {
            "messages": messages,
            "has_older": has_older,
        }

    def _assignment_map(self, start_ms: int, end_ms: int) -> Dict[str, str]:
        """``client_msg_id`` → ``request_id`` from request_id_assignment events in
        ``[start_ms, end_ms)``. A chat-only load uses this to stamp the request_id
        onto user-input rows, which don't carry one themselves (it lives on the
        agent-outputs assignment event). Caller holds the DB lock."""
        rows = self._db.conn.execute(
            "SELECT payload FROM ws_messages WHERE channel = 'agent-outputs' "
            "AND created_at >= ? AND created_at < ? AND payload LIKE '%request_id_assignment%'",
            (start_ms, end_ms),
        ).fetchall()
        mapping: Dict[str, str] = {}
        for (payload_json,) in rows:
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("event_type") != "request_id_assignment":
                continue
            client_msg_id, request_id = payload.get("client_msg_id"), payload.get("request_id")
            if isinstance(client_msg_id, str) and isinstance(request_id, str):
                mapping[client_msg_id] = request_id
        return mapping

    @staticmethod
    def _stamp_request_ids(messages: List[Dict[str, Any]], assignments: Dict[str, str]) -> None:
        """Stamp ``request_id`` into a user input's meta from the assignment map,
        so the client can correlate a replayed chat without the agent-outputs."""
        if not assignments:
            return
        for message in messages:
            meta = message.get("meta")
            payload = message.get("payload")
            if not isinstance(meta, dict) or "request_id" in meta:
                continue
            if isinstance(payload, dict) and payload.get("request_id"):
                continue
            request_id = assignments.get(meta.get("client_msg_id"))
            if request_id:
                meta["request_id"] = request_id

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        row_id, channel, payload_json, meta_json, created_at = row
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
        return {
            "id": row_id,
            "channel": channel,
            "payload": payload,
            "meta": meta,
            "created_at": created_at,
        }

    def close(self) -> None:
        # The connection belongs to StateDb; its owner closes it.
        pass


class NullMessageStore(MessageStore):
    """Discard-only store for tests and CLI mode."""

    def clear(self) -> None:
        pass

    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None,
               created_at: Optional[int] = None) -> None:
        pass

    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        return []

    def load_day(self, before_ms: int, channel: Optional[str] = None) -> Dict[str, Any]:
        return {"messages": [], "has_older": False}

    def load_range(self, start_ms: int, end_ms: int, channel: Optional[str] = None) -> Dict[str, Any]:
        return {"messages": [], "has_older": False}

    def close(self) -> None:
        pass


# Module-level singleton — callers use get/set, never import the instance directly
_store: MessageStore = NullMessageStore()


def get_message_store() -> MessageStore:
    return _store


def set_message_store(store: MessageStore) -> None:
    global _store  # noqa: PLW0603
    _store = store
