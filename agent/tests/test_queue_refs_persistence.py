"""Queue-reference persistence: the Q:<hex> queue references survive a
restart (manifest group B2), so "remove that one" still resolves.

The hex references are Swarpius-minted; on a fresh process the maps rebuild
from empty and would mint *different* hexes for the same queue items, so a
Q:<hex> the model was shown before the restart would resolve to "unknown".
Persisting the qid<->hex maps keeps surviving items on their original hexes
across the re-subscription that replays the queue.

The fake connection subclasses the real RoonEventsMixin (so resolve /
reconcile / map creation are production code) and only supplies the two
containers the mixin expects. The round-trip goes through
RuntimeState.attach_roon_persistence, so it does not encode participant count.
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
from roon_core.browse_session import BrowseSessionManager  # noqa: E402
from roon_core.events import RoonEventsMixin  # noqa: E402


class _FakeConn(RoonEventsMixin):
    """Real queue-reference logic; only the connection containers are stubbed."""

    def __init__(self) -> None:
        self.session_manager = BrowseSessionManager()
        self._queue_ref_maps: dict = {}


class TestQueueRefsPersistence(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _save(self, conn: _FakeConn) -> None:
        runtime = RuntimeState()
        runtime.roon_connection = conn
        manager = PersistenceManager(self.db)
        runtime.attach_roon_persistence(manager)
        manager.commit()

    def _restore_into(self, conn: _FakeConn) -> None:
        runtime = RuntimeState()
        runtime.roon_connection = conn
        manager = PersistenceManager(self.db)
        runtime.attach_roon_persistence(manager)

    def test_hex_resolves_to_same_queue_item_after_restart(self):
        original = _FakeConn()
        hex_ref = original._get_or_create_ref_map("zone-1").mint(1001)
        self._save(original)

        restored = _FakeConn()
        self._restore_into(restored)
        self.assertEqual(restored.resolve_queue_ref(hex_ref), 1001)

    def test_hex_preserved_across_reconcile_after_restart(self):
        original = _FakeConn()
        hex_ref = original._get_or_create_ref_map("zone-1").mint(1001)
        self._save(original)

        restored = _FakeConn()
        self._restore_into(restored)
        # Re-subscription replays the full queue; an item still present must
        # keep its original hex rather than being re-minted.
        restored_map = restored.get_queue_references("zone-1")
        self.assertIsNotNone(restored_map)
        restored_map.reconcile_full_list(
            [{"queue_item_id": 1001}, {"queue_item_id": 1002}],
        )
        self.assertEqual(restored_map.get_ref(1001), hex_ref)

    def test_invalidated_ref_message_survives_restart(self):
        original = _FakeConn()
        ref_map = original._get_or_create_ref_map("zone-1")
        hex_ref = ref_map.mint(2002)
        ref_map.invalidate(2002, "So What")
        self._save(original)

        restored = _FakeConn()
        self._restore_into(restored)
        qid, err = restored.get_queue_references("zone-1").resolve(hex_ref)
        self.assertIsNone(qid)
        self.assertIn("removed", err)


if __name__ == "__main__":
    unittest.main()
