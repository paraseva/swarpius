"""Tests for MessageStore implementations."""

import tempfile
import unittest
from pathlib import Path

from app.io.message_store import NullMessageStore, SqliteMessageStore


class TestSqliteMessageStore(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.store = SqliteMessageStore(self._tmp.name)

    def tearDown(self):
        self.store.close()
        Path(self._tmp.name).unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(self._tmp.name + suffix).unlink(missing_ok=True)

    def test_empty_store_returns_no_messages(self):
        self.assertEqual(self.store.get_all(), [])

    def test_append_and_retrieve(self):
        self.store.append("chat", {"text": "hello"})
        messages = self.store.get_all()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["channel"], "chat")
        self.assertEqual(messages[0]["payload"], {"text": "hello"})
        self.assertIsNone(messages[0]["meta"])

    def test_append_with_meta(self):
        self.store.append("chat", {"text": "hi"}, meta={"speak_text": "hi"})
        messages = self.store.get_all()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["meta"], {"speak_text": "hi"})

    def test_multiple_messages_preserve_order(self):
        self.store.append("chat", {"n": 1})
        self.store.append("agent-outputs", {"n": 2})
        self.store.append("chat", {"n": 3})
        messages = self.store.get_all()
        self.assertEqual(len(messages), 3)
        self.assertEqual([m["payload"]["n"] for m in messages], [1, 2, 3])
        self.assertEqual(
            [m["channel"] for m in messages],
            ["chat", "agent-outputs", "chat"],
        )

    def test_clear_removes_all_messages(self):
        self.store.append("chat", {"text": "a"})
        self.store.append("chat", {"text": "b"})
        self.assertEqual(len(self.store.get_all()), 2)
        self.store.clear()
        self.assertEqual(self.store.get_all(), [])

    def test_complex_payload_round_trips(self):
        payload = {
            "items": [{"title": "Track A", "ref": "abc12"}],
            "count": 1,
            "nested": {"deep": True},
        }
        self.store.append("tool-outputs", payload)
        retrieved = self.store.get_all()[0]["payload"]
        self.assertEqual(retrieved, payload)

    def test_non_serialisable_values_survive_roundtrip(self):
        """The store's ``default=str`` JSON fallback must let
        non-serialisable values (e.g. Path) survive an append+get_all
        round-trip without raising. The exact textual form of the
        converted value is an implementation detail of ``json.dumps``
        and not asserted — just that the payload is retrievable and
        the key is intact."""
        self.store.append("errors", {"path": Path("/tmp/test"), "code": 42})
        messages = self.store.get_all()
        self.assertEqual(len(messages), 1)
        payload = messages[0]["payload"]
        self.assertIn("path", payload)
        self.assertIn("code", payload)
        self.assertEqual(payload["code"], 42)

    def test_get_all_since_ms_filters_older_messages(self):
        """The websocket replay path bounds the per-connect history with
        ``since_ms``. Anything older than the cutoff stays on disk but
        is not returned."""
        self.store.append("chat", {"text": "old"})
        # Push the existing row's created_at back so we can assert the
        # filter without sleeping. Cutoff is set strictly above the row.
        self.store._conn.execute("UPDATE ws_messages SET created_at = 1000")
        self.store._conn.commit()
        self.store.append("chat", {"text": "new"})

        all_messages = self.store.get_all()
        self.assertEqual([m["payload"]["text"] for m in all_messages], ["old", "new"])

        recent = self.store.get_all(since_ms=2000)
        self.assertEqual([m["payload"]["text"] for m in recent], ["new"])

    def test_user_chat_persists_with_client_centric_outbound_direction(self):
        """Pins the convention established at websocket_flow.py:635-646.

        User chat arrives from the browser and is persisted with
        meta={"direction": "outbound"}. This uses the frontend's
        client-centric vocabulary ("outbound" = user bubble, sent OUT
        of the browser), not server-centric networking terminology.
        The convention flows through:
          - websocket_flow.py persists with direction="outbound"
          - WebSocketProvider.tsx:182 recognises replayed-outbound
          - ChatPanel.tsx:238 renders "outbound" as the "You" bubble

        Regressing this to "inbound" would make refresh-replayed user
        messages render as Swarpius's replies. Earlier reviews flagged
        the apparent server-centric mismatch as a bug — it's not.
        """
        self.store.append(
            "chat",
            {"channel": "chat", "body": "hello swarpius"},
            meta={"direction": "outbound"},
        )
        messages = self.store.get_all()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["meta"], {"direction": "outbound"})
        self.assertEqual(messages[0]["payload"]["body"], "hello swarpius")


class TestNullMessageStore(unittest.TestCase):

    def test_append_and_get_all_returns_empty(self):
        store = NullMessageStore()
        store.append("chat", {"text": "hello"})
        self.assertEqual(store.get_all(), [])

    def test_clear_does_not_raise(self):
        store = NullMessageStore()
        store.clear()

    def test_close_does_not_raise(self):
        store = NullMessageStore()
        store.close()


if __name__ == "__main__":
    unittest.main()
