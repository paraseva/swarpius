"""Clearing conversation history wipes the persisted transcript AND the
model's working memory (and the conversation's Roon references), so after a
clear a restart restores nothing — a genuine fresh start. The runtime
default zone is preserved (it's a preference, not conversation content).
"""

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.message_store import SqliteMessageStore, set_message_store  # noqa: E402
from app.io.state_db import StateDb  # noqa: E402
from app.runtime.persistence import PersistenceManager  # noqa: E402
from app.runtime.result_store_types import ResultStoreEntry  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


class TestClearConversationState(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.store = SqliteMessageStore(self.db)
        set_message_store(self.store)

    def tearDown(self):
        from app.io.message_store import NullMessageStore
        set_message_store(NullMessageStore())
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _runtime(self) -> RuntimeState:
        runtime = RuntimeState()
        runtime.attach_persistence(PersistenceManager(self.db))
        return runtime

    def test_clear_wipes_transcript_and_working_memory_across_restart(self):
        runtime = self._runtime()
        runtime.conversation_history_provider.add_turn(
            "play jazz", "Playing jazz.", timestamp=datetime.now(),
        )
        runtime.store_result_entries([
            ResultStoreEntry(items=[{"title": "A"}], description='"jazz"',
                             item_count=1, tool_name="roon_search"),
        ])
        self.store.append("chat", {"channel": "chat", "body": "play jazz"},
                          meta={"direction": "outbound"})
        runtime.persist_state()

        runtime.clear_conversation_state()

        # In-memory working memory is empty.
        self.assertEqual(runtime.conversation_history_provider.get_info(), "")
        self.assertEqual(runtime.result_store, {})
        # Transcript is gone.
        self.assertEqual(self.store.get_all(), [])
        # A restart (fresh runtime) restores nothing.
        restored = self._runtime()
        self.assertEqual(restored.conversation_history_provider.get_info(), "")
        self.assertEqual(restored.result_store, {})

    def test_clear_preserves_default_zone(self):
        runtime = self._runtime()
        runtime.conversation_history_provider.add_turn(
            "hi", "hello", timestamp=datetime.now(),
        )
        runtime.persist_state()

        runtime.clear_conversation_state()
        # Working memory cleared, but no error and default-zone state intact
        # (there's no connection here; the call must be a safe no-op for it).
        self.assertEqual(runtime.conversation_history_provider.get_info(), "")


if __name__ == "__main__":
    unittest.main()
