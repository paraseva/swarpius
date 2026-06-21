"""AppIO.ws_send persist-gating + meta inclusion.

Contract: messages on persisted channels (chat) are written to the message
store; transient channels (zone-snapshots) are not; the broadcast payload
carries a 'meta' key only when meta is truthy.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.io.core import AppIO  # noqa: E402
from app.io.message_store import (  # noqa: E402
    MessageStore,
    NullMessageStore,
    set_message_store,
)


class _CapturingStore(MessageStore):
    def __init__(self):
        self.appended = []

    def clear(self):
        pass

    def append(self, channel, payload, meta=None):
        self.appended.append((channel, payload, meta))

    def get_all(self, since_ms=None):
        return []

    def load_day(self, before_ms):
        return {"messages": [], "has_older": False}

    def load_range(self, start_ms, end_ms):
        return {"messages": [], "has_older": False}

    def close(self):
        pass


class TestAppIOWsSend(unittest.TestCase):
    def setUp(self):
        self.store = _CapturingStore()
        set_message_store(self.store)
        self.broadcasts = []
        self.appio = AppIO(
            run_mode_getter=lambda: "ws",
            console_getter=lambda: None,
            speak_text_coro=None,
            ws_clients=set(),
            get_ws_event_loop=lambda: None,
        )
        # Capture what ws_send hands to the broadcast layer.
        self.appio.ws_broadcast = lambda msg: self.broadcasts.append(msg)

    def tearDown(self):
        set_message_store(NullMessageStore())

    def test_persists_chat_channel(self):
        self.appio.ws_send("chat", {"body": "hi"})
        self.assertIn("chat", [c for c, _, _ in self.store.appended])

    def test_does_not_persist_transient_channel(self):
        self.appio.ws_send("zone-snapshots", {"zones": []})
        self.assertNotIn("zone-snapshots", [c for c, _, _ in self.store.appended])

    def test_broadcast_includes_meta_only_when_truthy(self):
        self.appio.ws_send("chat", {"body": "hi"}, meta={"request_id": "rq-1"})
        self.appio.ws_send("chat", {"body": "bye"})
        self.assertEqual(self.broadcasts[0].get("meta"), {"request_id": "rq-1"})
        self.assertNotIn("meta", self.broadcasts[1])


if __name__ == "__main__":
    unittest.main()
