"""The deterministic persist hook: RuntimeState.persist_state commits the
registered participants at request completion, and skips when a restart has
been requested (so an in-flight request terminated by the restart is dropped
and the restore boundary stays at the last request that completed before it).
"""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.state_db import StateDb  # noqa: E402
from app.runtime import restart_signal  # noqa: E402
from app.runtime.persistence import PersistenceManager  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


class TestPersistStateHook(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        restart_signal.clear()

    def tearDown(self):
        restart_signal.clear()
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _runtime(self) -> RuntimeState:
        runtime = RuntimeState()
        runtime.attach_persistence(PersistenceManager(self.db))
        return runtime

    def test_persist_state_commits_working_memory(self):
        runtime = self._runtime()
        runtime.conversation_history_provider.add_turn(
            "play jazz", "Playing jazz.",
            timestamp=datetime.now() - timedelta(hours=1),
        )
        runtime.persist_state()

        restored = self._runtime()
        self.assertTrue(restored.conversation_history_provider.get_info())

    def test_persist_state_is_skipped_during_restart(self):
        runtime = self._runtime()
        runtime.conversation_history_provider.add_turn(
            "play jazz", "Playing jazz.",
            timestamp=datetime.now() - timedelta(hours=1),
        )
        restart_signal.request_restart()
        runtime.persist_state()

        # Nothing was committed, so a fresh runtime restores empty.
        restored = self._runtime()
        self.assertFalse(restored.conversation_history_provider.get_info())

    def test_persist_state_is_noop_without_manager(self):
        # A runtime that never attached persistence must not raise.
        RuntimeState().persist_state()


if __name__ == "__main__":
    unittest.main()
