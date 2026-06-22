"""Working-memory persistence: the coordinator's cross-turn memory survives
a restart (manifest group A).

These tests operate at the RuntimeState level and assert *observable*
behaviour — the rendered conversation/search sections the model sees, and
that a result handle still resolves. They never reference how many
participants back the runtime's working memory, so if that is later split or
merged the tests stay unchanged and validate the refactor.

The round-trip is: populate a RuntimeState, save via the persistence
manager, build a *fresh* RuntimeState that restores from the same DB, and
assert the restored runtime presents the same working memory as the original.
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
from app.runtime.persistence import PersistenceManager  # noqa: E402
from app.runtime.result_store_types import ResultStoreEntry  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


class TestWorkingMemoryPersistence(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _fresh_runtime(self) -> tuple[RuntimeState, PersistenceManager]:
        manager = PersistenceManager(self.db)
        runtime = RuntimeState()
        runtime.attach_persistence(manager)
        return runtime, manager

    def _populate_and_save(self) -> RuntimeState:
        """Build a runtime, populate its working memory, save it, and return
        the original (in-memory) runtime so tests can compare a restored one
        against it — a genuine save/restore round-trip."""
        runtime, manager = self._fresh_runtime()
        # A two-hours-old turn so the rendered relative time ("2 hr ago") is
        # stable across the milliseconds between the two get_info() calls.
        turn_ts = datetime.now() - timedelta(hours=2)
        runtime.conversation_history_provider.add_turn(
            "play some miles davis", "Playing Kind of Blue.", timestamp=turn_ts,
        )
        runtime.store_result_entries([
            ResultStoreEntry(
                items=[{"title": "Kind of Blue", "reference": "S:abc12"}],
                description='"miles davis"',
                item_count=1,
                tool_name="roon_search",
            ),
        ])
        runtime.execution_trace.append(
            {"step": 1, "selected_skill": "roon_search", "note": "searched miles davis"},
        )
        runtime.global_step = 3
        runtime.set_prompt_state_context()
        manager.commit()
        return runtime

    def test_conversation_section_renders_identically_after_restart(self):
        original = self._populate_and_save()
        restored, _ = self._fresh_runtime()
        expected = original.conversation_history_provider.get_info()
        self.assertTrue(expected, "precondition: original conversation is non-empty")
        self.assertEqual(restored.conversation_history_provider.get_info(), expected)

    def test_search_history_section_renders_identically_after_restart(self):
        original = self._populate_and_save()
        restored, _ = self._fresh_runtime()
        restored.set_prompt_state_context()
        expected = original.search_history_provider.get_info()
        self.assertTrue(expected, "precondition: original search history is non-empty")
        self.assertEqual(restored.search_history_provider.get_info(), expected)

    def test_result_handle_resolves_to_same_payload_after_restart(self):
        runtime, manager = self._fresh_runtime()
        [handle] = runtime.store_result_entries([
            ResultStoreEntry(
                items=[{"title": "Blue Train"}],
                description='"coltrane"',
                item_count=1,
                tool_name="roon_search",
            ),
        ])
        expected_payload = runtime.result_store[handle]
        manager.commit()

        restored, _ = self._fresh_runtime()
        self.assertEqual(restored.result_store[handle], expected_payload)

    def test_execution_trace_and_step_counter_survive_restart(self):
        runtime, manager = self._fresh_runtime()
        runtime.execution_trace.append({"step": 1, "note": "searched"})
        runtime.global_step = 7
        manager.commit()

        restored, _ = self._fresh_runtime()
        self.assertEqual(restored.execution_trace, runtime.execution_trace)
        self.assertEqual(restored.global_step, runtime.global_step)


if __name__ == "__main__":
    unittest.main()
