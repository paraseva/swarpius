"""SqliteMessageStore.load_day: the lazy-load primitive — the most recent
non-empty calendar day at or before a cursor, skipping empty days, with a
'has_older' flag and a stable per-row id for FE ordering/dedup.
"""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.io.message_store import SqliteMessageStore
from app.io.state_db import StateDb


def _ms(days_ago: int, hour: int = 10) -> int:
    d = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0,
    )
    return int(d.timestamp() * 1000)


class TestLoadDay(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.store = SqliteMessageStore(self.db)
        # today (two messages), 2 days ago, 4 days ago — gaps at 1 and 3 days.
        self._insert("chat", _ms(0, 10), "today-a")
        self._insert("chat", _ms(0, 11), "today-b")
        self._insert("chat", _ms(2, 10), "twodays")
        self._insert("chat", _ms(4, 10), "fourdays")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _insert(self, channel: str, created_at: int, body: str) -> None:
        self.db.conn.execute(
            "INSERT INTO ws_messages (channel, payload, created_at) VALUES (?, ?, ?)",
            (channel, f'{{"body": "{body}"}}', created_at),
        )
        self.db.conn.commit()

    def _bodies(self, result) -> list:
        return [m["payload"]["body"] for m in result["messages"]]

    def test_load_day_now_returns_today_with_has_older(self):
        result = self.store.load_day(_ms(0, 23))
        self.assertEqual(self._bodies(result), ["today-a", "today-b"])
        self.assertTrue(result["has_older"])
        # rows carry a stable id for FE ordering/dedup.
        self.assertIn("id", result["messages"][0])

    def test_scroll_back_skips_empty_days(self):
        # cursor just before today's oldest → previous non-empty day is 2 days
        # ago (1 day ago is empty and must be skipped).
        result = self.store.load_day(_ms(0, 10) - 1)
        self.assertEqual(self._bodies(result), ["twodays"])
        self.assertTrue(result["has_older"])

    def test_oldest_day_reports_no_older(self):
        result = self.store.load_day(_ms(2, 10) - 1)
        self.assertEqual(self._bodies(result), ["fourdays"])
        self.assertFalse(result["has_older"])

    def test_before_all_history_is_empty(self):
        result = self.store.load_day(_ms(4, 10) - 1)
        self.assertEqual(result["messages"], [])
        self.assertFalse(result["has_older"])


if __name__ == "__main__":
    unittest.main()
