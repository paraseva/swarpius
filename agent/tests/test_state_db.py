"""Tests for StateDb — the shared connection handle for the state DB.

StateDb owns the single SQLite connection and the lock that serialises all
writers, so a multi-statement write (a request's transcript rows + its state
snapshot) can commit atomically: while the lock is held no other writer can
slip a commit onto the shared connection. These tests pin that transaction
contract against a real temp DB.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.io.state_db import StateDb


class TestStateDbTransaction(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_opens_with_migrated_schema(self):
        """StateDb opens through the versioned-DB path, so the v1 tables
        exist immediately."""
        tables = {
            r[0]
            for r in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
        self.assertIn("agent_state", tables)

    def test_transaction_commits_on_success(self):
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_state (state_key, payload, updated_at) "
                "VALUES ('k', 'v', 1)",
            )
        rows = self.db.conn.execute(
            "SELECT payload FROM agent_state WHERE state_key='k'",
        ).fetchall()
        self.assertEqual(rows, [("v",)])

    def test_transaction_rolls_back_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO agent_state (state_key, payload, updated_at) "
                    "VALUES ('k', 'v', 1)",
                )
                raise RuntimeError("boom")
        rows = self.db.conn.execute(
            "SELECT payload FROM agent_state WHERE state_key='k'",
        ).fetchall()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
