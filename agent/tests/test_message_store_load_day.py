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

    def test_load_day_chat_stamps_user_input_request_id(self):
        # A chat user input carries no request_id of its own — it's on the
        # agent-outputs assignment event, matched by client_msg_id. A chat-only
        # load must stamp it back on so the client can correlate without the
        # agent-outputs rows.
        cid = "cmid-1"
        self.db.conn.execute(
            "INSERT INTO ws_messages (channel, payload, meta, created_at) VALUES (?, ?, ?, ?)",
            ("chat", '{"channel": "chat", "body": "play jazz"}',
             f'{{"direction": "outbound", "client_msg_id": "{cid}"}}', _ms(0, 9)),
        )
        self.db.conn.execute(
            "INSERT INTO ws_messages (channel, payload, created_at) VALUES (?, ?, ?)",
            ("agent-outputs",
             f'{{"event_type": "request_id_assignment", "client_msg_id": "{cid}", "request_id": "rq-c01-0007"}}',
             _ms(0, 9) + 1),
        )
        self.db.conn.commit()
        result = self.store.load_day(_ms(0, 23), channel="chat")
        user_input = next(m for m in result["messages"] if m["payload"].get("body") == "play jazz")
        self.assertEqual(user_input["meta"]["request_id"], "rq-c01-0007")

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

    def test_load_day_filtered_by_channel(self):
        # A sparse channel: one 'errors' message, on a day older than the chat
        # days. Filtering to 'errors' must find that day, skipping the more
        # recent chat-only days, and report no older errors.
        self._insert("errors", _ms(6, 10), "err-old")
        result = self.store.load_day(_ms(0, 23), channel="errors")
        self.assertEqual(self._bodies(result), ["err-old"])
        self.assertFalse(result["has_older"])
        # Unfiltered still returns the most recent (chat) day.
        self.assertEqual(self._bodies(self.store.load_day(_ms(0, 23))), ["today-a", "today-b"])

    def test_oldest_day_reports_no_older(self):
        result = self.store.load_day(_ms(2, 10) - 1)
        self.assertEqual(self._bodies(result), ["fourdays"])
        self.assertFalse(result["has_older"])

    def test_before_all_history_is_empty(self):
        result = self.store.load_day(_ms(4, 10) - 1)
        self.assertEqual(result["messages"], [])
        self.assertFalse(result["has_older"])

    def test_load_range_returns_everything_in_window_chronologically(self):
        # [4 days ago 00:00, today 00:00) → the 4-days then 2-days messages,
        # oldest first, excluding today; nothing older than the start.
        result = self.store.load_range(_ms(4, 0), _ms(0, 0))
        self.assertEqual(self._bodies(result), ["fourdays", "twodays"])
        self.assertFalse(result["has_older"])

    def test_load_range_reports_older_when_earlier_history_exists(self):
        result = self.store.load_range(_ms(2, 0), _ms(0, 0))
        self.assertEqual(self._bodies(result), ["twodays"])
        self.assertTrue(result["has_older"])


if __name__ == "__main__":
    unittest.main()
