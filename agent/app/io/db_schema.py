"""Versioned schema + migration runner for the shared state DB.

The state-persistence feature stores its tables in the same SQLite file as
the WS message store. A standalone ``PRAGMA user_version`` (decoupled from
the Swarpius release version) governs the schema, advanced by a chain of
stepwise ``N -> N+1`` migrations applied in sequence — so a DB stuck at v0
upgrades through every intermediate step without the user having to install
intermediate Swarpius versions.

:func:`open_versioned_db` is the safe entry point: a DB that is corrupt or
newer than this build understands is moved aside so the process still boots
on a clean DB rather than failing startup.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable, Dict

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1


class SchemaTooNewError(Exception):
    """The DB's ``user_version`` exceeds what this build can migrate.

    Raised by :func:`run_migrations`; :func:`open_versioned_db` handles it
    by backing up the DB and starting fresh.
    """

    def __init__(self, found: int, supported: int) -> None:
        super().__init__(
            f"DB schema version {found} is newer than the supported version {supported}",
        )
        self.found = found
        self.supported = supported


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str,
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_0_to_1(conn: sqlite3.Connection) -> None:
    """Create the v1 schema: the transcript table (also created lazily by
    the message store), the per-participant restore-state table, and the
    queryable listening-history table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ws_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            payload TEXT NOT NULL,
            meta TEXT,
            created_at INTEGER NOT NULL
        )
        """,
    )
    # Grouping columns for history lazy-load / chat<->diagnostics jump-to.
    _add_column_if_missing(conn, "ws_messages", "request_id", "TEXT")
    _add_column_if_missing(conn, "ws_messages", "status", "TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_state (
            state_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """,
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS listening_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            zone TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration INTEGER
        )
        """,
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_listening_history_ts ON listening_history(ts)",
    )


# from-version -> migration that advances it to from-version + 1.
_MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _migrate_0_to_1,
}


def run_migrations(conn: sqlite3.Connection) -> int:
    """Advance ``conn`` to :data:`CURRENT_SCHEMA_VERSION`, applying each
    registered ``N -> N+1`` step in sequence. Returns the resulting version.

    Raises :class:`SchemaTooNewError` if the DB is newer than this build.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > CURRENT_SCHEMA_VERSION:
        raise SchemaTooNewError(version, CURRENT_SCHEMA_VERSION)
    while version < CURRENT_SCHEMA_VERSION:
        _MIGRATIONS[version](conn)
        version += 1
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    return version


def _back_up(path: Path) -> Path:
    """Move ``path`` aside to a non-clobbering ``.bak`` sibling, return it."""
    candidate = path.with_name(path.name + ".bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak{counter}")
        counter += 1
    path.replace(candidate)
    return candidate


def open_versioned_db(path: Path | str) -> sqlite3.Connection:
    """Open ``path`` and bring it to the current schema version.

    A DB that is corrupt or newer than this build understands is moved aside
    (preserved as a ``.bak`` sibling) and a fresh DB is created in its place,
    so a damaged or future DB never blocks startup.
    """
    path = Path(path)

    def _connect() -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        run_migrations(conn)
        return conn

    try:
        return _connect()
    except SchemaTooNewError as exc:
        logger.warning(
            "State DB at %s is newer than this build (v%d > v%d); backing up and starting fresh",
            path, exc.found, exc.supported,
        )
    except sqlite3.DatabaseError as exc:
        logger.warning(
            "State DB at %s is unreadable (%s); backing up and starting fresh", path, exc,
        )

    if path.exists():
        backup = _back_up(path)
        logger.warning("Previous state DB preserved at %s", backup)
    # Sidecars belong to the moved-aside file's journal; drop any stragglers.
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return _connect()
