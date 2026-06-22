"""Tests for the versioned-DB schema + migration runner.

The state-persistence feature shares one SQLite file with the WS message
store, governed by a standalone ``PRAGMA user_version`` and a chain of
stepwise migrations. These tests pin the runner's observable contract:
a fresh DB reaches the current version with all v1 tables; an existing
pre-versioning (v0) DB is upgraded without losing its rows; and an
unusable DB (newer-than-known, or corrupt) is moved aside so the process
still boots on a clean DB.

Each test gets its own temp directory, removed (with any -wal/-shm
sidecars) on teardown.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.io.db_schema import (
    CURRENT_SCHEMA_VERSION,
    SchemaTooNewError,
    open_versioned_db,
    run_migrations,
)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


class TestRunMigrations(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_fresh_db_reaches_current_version_with_v1_tables(self):
        conn = sqlite3.connect(str(self._dir / "state.db"))
        try:
            result = run_migrations(conn)
            self.assertEqual(result, CURRENT_SCHEMA_VERSION)
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
            tables = _table_names(conn)
            self.assertIn("ws_messages", tables)
            self.assertIn("agent_state", tables)
            self.assertIn("listening_history", tables)
            self.assertIn("cost_ledger", tables)
            # Bucket-2 grouping columns present on the transcript table.
            ws_cols = _column_names(conn, "ws_messages")
            self.assertIn("request_id", ws_cols)
            self.assertIn("status", ws_cols)
        finally:
            conn.close()

    def test_v0_db_is_upgraded_without_losing_rows(self):
        """An existing pre-versioning DB has ws_messages (old schema) and
        user_version 0. Migrating must add the new columns/tables and
        preserve every existing row."""
        path = self._dir / "state.db"
        seed = sqlite3.connect(str(path))
        seed.execute(
            """
            CREATE TABLE ws_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                payload TEXT NOT NULL,
                meta TEXT,
                created_at INTEGER NOT NULL
            )
            """,
        )
        seed.execute(
            "INSERT INTO ws_messages (channel, payload, meta, created_at) "
            "VALUES ('chat', '{\"body\": \"hello\"}', NULL, 1000)",
        )
        seed.commit()
        self.assertEqual(_user_version(seed), 0)
        seed.close()

        conn = sqlite3.connect(str(path))
        try:
            run_migrations(conn)
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
            # Pre-existing row survived.
            rows = conn.execute("SELECT channel, payload FROM ws_messages").fetchall()
            self.assertEqual(rows, [("chat", '{"body": "hello"}')])
            # New columns + tables added alongside.
            self.assertIn("request_id", _column_names(conn, "ws_messages"))
            self.assertIn("agent_state", _table_names(conn))
            self.assertIn("listening_history", _table_names(conn))
        finally:
            conn.close()

    def test_v1_db_gains_cost_ledger_on_upgrade(self):
        """An existing v1 DB (no cost_ledger) gains it via the 1->2 step."""
        from app.io.db_schema import _migrate_0_to_1
        path = self._dir / "state.db"
        seed = sqlite3.connect(str(path))
        _migrate_0_to_1(seed)
        seed.execute("PRAGMA user_version = 1")
        seed.commit()
        self.assertNotIn("cost_ledger", _table_names(seed))
        seed.close()

        conn = sqlite3.connect(str(path))
        try:
            run_migrations(conn)
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
            self.assertIn("cost_ledger", _table_names(conn))
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        path = self._dir / "state.db"
        conn = sqlite3.connect(str(path))
        try:
            run_migrations(conn)
            # Running again is a no-op (already at current version).
            self.assertEqual(run_migrations(conn), CURRENT_SCHEMA_VERSION)
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
        finally:
            conn.close()

    def test_newer_than_known_version_raises(self):
        path = self._dir / "state.db"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 5}")
            conn.commit()
            with self.assertRaises(SchemaTooNewError):
                run_migrations(conn)
        finally:
            conn.close()


class TestOpenVersionedDb(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_open_fresh_db_is_migrated_and_usable(self):
        conn = open_versioned_db(self._dir / "state.db")
        try:
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
            self.assertIn("agent_state", _table_names(conn))
        finally:
            conn.close()

    def test_newer_than_known_db_is_backed_up_and_started_fresh(self):
        path = self._dir / "state.db"
        seed = sqlite3.connect(str(path))
        seed.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 5}")
        seed.execute("CREATE TABLE leftover (x INTEGER)")
        seed.commit()
        seed.close()

        conn = open_versioned_db(path)
        try:
            # Booted clean at the current version.
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
            self.assertIn("agent_state", _table_names(conn))
            # The unusable DB was preserved, not silently destroyed.
            backups = list(self._dir.glob("state.db.bak*"))
            self.assertTrue(backups, "expected the too-new DB to be backed up")
        finally:
            conn.close()

    def test_corrupt_db_is_backed_up_and_boots_clean_with_warning(self):
        path = self._dir / "state.db"
        path.write_bytes(b"this is not a sqlite database at all")

        with self.assertLogs(level="WARNING"):
            conn = open_versioned_db(path)
        try:
            self.assertEqual(_user_version(conn), CURRENT_SCHEMA_VERSION)
            self.assertIn("agent_state", _table_names(conn))
            backups = list(self._dir.glob("state.db.bak*"))
            self.assertTrue(backups, "expected the corrupt DB to be backed up")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
