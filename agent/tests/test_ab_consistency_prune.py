"""A/B consistency (Decision 14): the model's working memory must never
contain turns the transcript no longer has. The transcript is pruned at the
chat-retention window, so on restore any conversation turn older than that
window is dropped from working memory too — a timestamp comparison, no
transcript query.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.state_db import StateDb  # noqa: E402
from app.runtime.persistence import PersistenceManager  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


class TestABConsistencyPrune(unittest.TestCase):

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

    def test_turns_older_than_chat_retention_dropped_on_restore(self):
        with patch.dict(os.environ, {"CHAT_HISTORY_RETENTION_DAYS": "1"}):
            original = self._runtime()
            original.conversation_history_provider.add_turn(
                "ancient question", "ancient answer",
                timestamp=datetime.now() - timedelta(days=3),
            )
            original.conversation_history_provider.add_turn(
                "recent question", "recent answer",
                timestamp=datetime.now(),
            )
            original.persist_state()

            restored = self._runtime()
            info = restored.conversation_history_provider.get_info()
            self.assertNotIn("ancient", info)
            self.assertIn("recent", info)

    def test_zero_retention_keeps_all_turns(self):
        with patch.dict(os.environ, {"CHAT_HISTORY_RETENTION_DAYS": "0"}):
            original = self._runtime()
            original.conversation_history_provider.add_turn(
                "ancient question", "ancient answer",
                timestamp=datetime.now() - timedelta(days=3000),
            )
            original.persist_state()

            restored = self._runtime()
            self.assertIn("ancient", restored.conversation_history_provider.get_info())


if __name__ == "__main__":
    unittest.main()
