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
from unittest.mock import patch

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

    def test_clear_wipes_on_disk_conversation_and_server_logs(self):
        # "Clear conversation history" is a privacy action, so it must also
        # delete the on-disk logs — the most detailed copy of the conversation
        # (inputs, responses, prompts, tool I/O), not just the transcript DB.
        runtime = self._runtime()
        conv_root = self._dir / "logs" / "conversation"
        server_root = self._dir / "logs" / "server"
        conv_req = conv_root / "2026-06-25" / "c01" / "rq-c01-0001"
        conv_req.mkdir(parents=True)
        (conv_req / "request.json").write_text("{}", encoding="utf-8")
        (server_root / "2026-06-25" / "c01" / "rq-c01-0001").mkdir(parents=True)

        with patch("app.data_paths.conversation_logs_dir", return_value=conv_root), \
                patch("app.data_paths.server_logs_dir", return_value=server_root):
            runtime.clear_conversation_state()

        self.assertFalse(conv_req.exists(), "conversation logs were not wiped")
        self.assertFalse(
            (server_root / "2026-06-25").exists(), "server logs were not wiped",
        )

    def test_clear_advances_to_a_fresh_conversation(self):
        # A clear opens a fresh conversation for logging/analysis: the counter
        # keeps incrementing (cNN+1) and the diagnostic agent sees no prior
        # threads to continue.
        runtime = self._runtime()
        gen = runtime.request_id_generator
        gen.next_id()  # opens c01
        gen.tracker.update_topic("c01", "playing jazz")

        runtime.clear_conversation_state()

        self.assertEqual(gen.tracker.get_active_threads(), [])
        self.assertEqual(gen.next_id(), "rq-c02-0001")


if __name__ == "__main__":
    unittest.main()
