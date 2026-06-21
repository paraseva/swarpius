"""Conversation grouping survives a restart: the process-level generator's
state (ID counters + tracker threads/topics/last-request-time) round-trips
through the persistence layer, so a continued topic keeps its cNN and the
next new conversation continues the numbering.

Routes through RuntimeState.attach_persistence (the production path), so it
does not encode participant count, and validates that the tracker — now
process-level rather than per-connection — is what gets restored.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.state_db import StateDb  # noqa: E402
from app.runtime.persistence import PersistenceManager  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


class TestConversationTrackerPersistence(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _runtime(self) -> RuntimeState:
        runtime = RuntimeState()
        runtime.attach_persistence(PersistenceManager(self.db))
        return runtime

    def test_attach_persistence_creates_a_process_level_generator(self):
        runtime = self._runtime()
        self.assertIsNotNone(runtime.request_id_generator)

    def test_conversation_threads_and_numbering_survive_restart(self):
        runtime = self._runtime()
        gen = runtime.request_id_generator
        gen.next_id()  # mints rq-c01-0001, opens conversation c01
        gen.tracker.update_topic("c01", "miles davis")
        runtime.persist_state()

        reborn = self._runtime()
        tracker = reborn.request_id_generator.tracker
        # Topic preserved on the restored thread.
        topics = {t.id: t.topic_summary for t in tracker.get_active_threads()}
        self.assertEqual(topics.get("c01"), "miles davis")
        # Numbering continues: a brand-new conversation is c02, not c01.
        reborn.request_id_generator.new_conversation()
        self.assertEqual(tracker.current_id, "c02")


if __name__ == "__main__":
    unittest.main()
