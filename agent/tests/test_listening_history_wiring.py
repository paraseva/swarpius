"""Attaching persistence wires a listening-history store backed by the shared
state DB, so recorded plays survive a restart and are queryable.
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


def _state(zone_id, display_name, line1):
    return {
        "type": "state",
        "zones": [{
            "zone_id": zone_id,
            "display_name": display_name,
            "now_playing": {"three_line": {"line1": line1}},
            "outputs": [],
        }],
    }


class TestListeningHistoryWiring(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_attach_persistence_creates_db_backed_store(self):
        runtime = RuntimeState()
        runtime.attach_persistence(PersistenceManager(self.db))
        self.assertIsNotNone(runtime.listening_history)

        runtime.listening_history.handle_event(_state("z1", "Kitchen", "So What"))

        # A fresh runtime over the same DB queries the recorded play.
        reborn = RuntimeState()
        reborn.attach_persistence(PersistenceManager(self.db))
        rows = reborn.listening_history.query()
        self.assertEqual([r["title"] for r in rows], ["So What"])


if __name__ == "__main__":
    unittest.main()
