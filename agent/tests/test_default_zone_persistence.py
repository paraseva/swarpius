"""Default-zone persistence: a runtime default-zone choice survives a
restart (manifest group C) instead of reverting to the boot-time seed.

Routes through RuntimeState.attach_roon_persistence (the production path),
so it does not encode participant count.
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
from tests._browse_fake import BrowseFake  # noqa: E402


class TestDefaultZonePersistence(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _save(self, conn: BrowseFake) -> None:
        runtime = RuntimeState()
        runtime.roon_connection = conn
        manager = PersistenceManager(self.db)
        runtime.attach_roon_persistence(manager)
        manager.commit()

    def _restore_into(self, conn: BrowseFake) -> None:
        runtime = RuntimeState()
        runtime.roon_connection = conn
        manager = PersistenceManager(self.db)
        runtime.attach_roon_persistence(manager)

    def test_runtime_default_zone_survives_restart(self):
        original = BrowseFake()
        original._preferred_output_id = "out-kitchen"
        original._preferred_zone_label = "Kitchen"
        self._save(original)

        restored = BrowseFake()
        self._restore_into(restored)
        self.assertEqual(restored._preferred_output_id, "out-kitchen")
        self.assertEqual(restored._preferred_zone_label, "Kitchen")

    def test_no_stored_default_leaves_seed_untouched(self):
        # Nothing saved: a connection that already seeded a default keeps it.
        self._save(BrowseFake())  # commits an empty (None) default

        restored = BrowseFake()
        restored._preferred_output_id = "out-seeded"
        restored._preferred_zone_label = "Seeded"
        self._restore_into(restored)
        self.assertEqual(restored._preferred_output_id, "out-seeded")


if __name__ == "__main__":
    unittest.main()
