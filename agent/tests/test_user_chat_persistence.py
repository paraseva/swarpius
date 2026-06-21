"""User-chat persistence at request completion (Decision 5 / append-at-terminal).

The user's message is persisted when the request reaches a non-restart
terminal, grouped with that request, rather than on receipt — so a restart
that drops the in-flight request leaves no orphaned message. It carries the
frontend's client-centric 'outbound' direction and is skipped while a restart
is pending.
"""

import unittest
from typing import Any, Dict, List, Optional

from app.coordinator.request_flow import _persist_user_chat
from app.io.message_store import MessageStore, NullMessageStore, set_message_store
from app.runtime import restart_signal


class _CapturingStore(MessageStore):
    def __init__(self) -> None:
        self.appended: List[tuple] = []

    def clear(self) -> None:
        pass

    def append(self, channel: str, payload: Any, meta: Optional[Dict[str, Any]] = None) -> None:
        self.appended.append((channel, payload, meta))

    def get_all(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        return []

    def load_day(self, before_ms: int) -> Dict[str, Any]:
        return {"messages": [], "has_older": False}

    def close(self) -> None:
        pass


class TestUserChatPersistence(unittest.TestCase):

    def setUp(self):
        restart_signal.clear()
        self.store = _CapturingStore()
        set_message_store(self.store)

    def tearDown(self):
        restart_signal.clear()
        set_message_store(NullMessageStore())

    def test_persists_with_outbound_meta_and_client_msg_id(self):
        _persist_user_chat("play some jazz", "cm-1")
        chat = [a for a in self.store.appended if a[0] == "chat"]
        self.assertEqual(len(chat), 1)
        _, payload, meta = chat[0]
        self.assertEqual(payload["body"], "play some jazz")
        self.assertEqual(meta.get("direction"), "outbound")
        self.assertEqual(meta.get("client_msg_id"), "cm-1")

    def test_omits_client_msg_id_when_absent(self):
        _persist_user_chat("hello", None)
        _, _, meta = self.store.appended[0]
        self.assertNotIn("client_msg_id", meta)

    def test_skipped_while_restart_pending(self):
        restart_signal.request_restart()
        _persist_user_chat("dropped by restart", "cm-2")
        self.assertEqual(self.store.appended, [])


if __name__ == "__main__":
    unittest.main()
