"""History retention sweep: prunes chat, diagnostics, and listening-history
rows older than their configured windows; a window of 0 means keep forever.
Chat and diagnostics have independent windows (diagnostics is bulkier and
shorter-lived); both live in ws_messages, distinguished by channel.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.io.history_retention import prune_history
from app.io.state_db import StateDb

_DAY_MS = 24 * 60 * 60 * 1000
_NOW = 1_000 * _DAY_MS  # arbitrary "now" well clear of zero


class TestHistoryRetention(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _msg(self, channel: str, age_days: int) -> None:
        self.db.conn.execute(
            "INSERT INTO ws_messages (channel, payload, created_at) VALUES (?, '{}', ?)",
            (channel, _NOW - age_days * _DAY_MS),
        )
        self.db.conn.commit()

    def _play(self, age_days: int) -> None:
        self.db.conn.execute(
            "INSERT INTO listening_history (ts, title) VALUES (?, 't')",
            (_NOW - age_days * _DAY_MS,),
        )
        self.db.conn.commit()

    def _count(self, table: str, where: str = "") -> int:
        return self.db.conn.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()[0]

    def test_prunes_each_store_by_its_own_window(self):
        self._msg("chat", 100)        # older than 90 → pruned
        self._msg("chat", 10)         # kept
        self._msg("agent-outputs", 40)  # diagnostics older than 30 → pruned
        self._msg("agent-outputs", 5)   # kept
        self._play(400)               # older than 365 → pruned
        self._play(10)                # kept

        prune_history(
            self.db, chat_days=90, diagnostics_days=30, listening_days=365, now_ms=_NOW,
        )

        self.assertEqual(self._count("ws_messages", " WHERE channel='chat'"), 1)
        self.assertEqual(self._count("ws_messages", " WHERE channel='agent-outputs'"), 1)
        self.assertEqual(self._count("listening_history"), 1)

    def test_zero_window_keeps_everything(self):
        self._msg("chat", 10_000)
        self._msg("agent-outputs", 10_000)
        self._play(10_000)

        prune_history(
            self.db, chat_days=0, diagnostics_days=0, listening_days=0, now_ms=_NOW,
        )

        self.assertEqual(self._count("ws_messages"), 2)
        self.assertEqual(self._count("listening_history"), 1)


if __name__ == "__main__":
    unittest.main()
