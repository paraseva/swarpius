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

CURRENT_SCHEMA_VERSION = 4


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


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Add the cost ledger: one row per LLM agent invocation, aggregated by the
    cost dashboard. Never pruned (cost rows are tiny and kept indefinitely)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cost_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            agent TEXT NOT NULL,
            model TEXT NOT NULL,
            request_id TEXT,
            conversation_id TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0
        )
        """,
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_ledger_ts ON cost_ledger(ts)")


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """Add steps to the cost ledger: the coordinator's tool-loop step count per
    request, so the dashboard can show mean cost per request by complexity.
    Null for sub-agent / analyser rows (steps don't apply)."""
    _add_column_if_missing(conn, "cost_ledger", "steps", "INTEGER")


def _migrate_3_to_4(conn: sqlite3.Connection) -> None:
    """Back-fill a request_complete for past failed requests. Older data
    predates emitting a completion on failure, so a failed request was recorded
    only on the errors channel — leaving it without the completion event the UI
    keys on, so it vanished (e.g. from Session Requests) instead of showing as
    failed. Add the missing completion (status=error, carrying the reason) for
    each '[Request]' error lacking one *that day* — ids reset daily, so the same
    id completed on one day and failed on another are distinct requests."""
    import json

    from app.runtime.request_logger import extract_conversation_dir
    from app.time_utils import local_day

    def day_of(created_at_ms: int) -> str:
        return local_day(created_at_ms / 1000)

    completed: set[tuple[str, str]] = set()
    for payload_json, created_at in conn.execute(
        "SELECT payload, created_at FROM ws_messages WHERE channel = 'agent-outputs' "
        "AND payload LIKE '%request_complete%'",
    ).fetchall():
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        rid = payload.get("request_id")
        if payload.get("event_type") == "request_complete" and rid:
            completed.add((rid, day_of(created_at)))

    for payload_json, created_at in conn.execute(
        "SELECT payload, created_at FROM ws_messages WHERE channel = 'errors' "
        "AND payload LIKE '%[Request]%'",
    ).fetchall():
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        rid = payload.get("request_id")
        if payload.get("source") != "[Request]" or not rid:
            continue
        key = (rid, day_of(created_at))
        if key in completed:
            continue
        completed.add(key)
        conn.execute(
            "INSERT INTO ws_messages (channel, payload, created_at) VALUES (?, ?, ?)",
            ("agent-outputs", json.dumps({
                "source": "[Request Complete]",
                "event_type": "request_complete",
                "request_id": rid,
                "total_steps": 0,
                "total_duration_ms": 0,
                "status": "error",
                "error": payload.get("error"),
                "conversation_id": extract_conversation_dir(rid),
            }), created_at),
        )


# from-version -> migration that advances it to from-version + 1.
_MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _migrate_0_to_1,
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
    3: _migrate_3_to_4,
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
