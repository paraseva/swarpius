"""Shared SQLite handle for the state DB (transcript + persisted state).

One SQLite file backs both the WS message store and the state-persistence
tables. ``StateDb`` owns the single connection and the lock that serialises
all writers, so a multi-statement write — a request's transcript rows plus
its state snapshot — commits atomically: while the lock is held no other
writer can interleave a commit on the shared connection.

The connection is opened through :func:`open_versioned_db`, so it is
WAL-mode, schema-migrated, and resilient to a corrupt / future DB.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.io.db_schema import open_versioned_db


class StateDb:
    """Owns the shared state-DB connection and the writer lock."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn = open_versioned_db(self._db_path)
        self._lock = threading.RLock()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a unit of work atomically: commit on success, roll back on
        any exception. Holds the writer lock for the whole unit so no other
        writer can commit on the shared connection mid-transaction."""
        with self._lock:
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
