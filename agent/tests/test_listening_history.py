"""Listening-history store: records each new track per zone to a queryable
table, so the coordinator can answer "what did I listen to last Tuesday".

Detection is per-zone (one entry per track played in a zone), distinct from
play_history's per-output "last played here" deque.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.io.state_db import StateDb
from app.roon.listening_history import ListeningHistoryStore


def _state(zone_id, display_name, line1, line2=None, line3=None, length=None):
    return {
        "type": "state",
        "zones": [{
            "zone_id": zone_id,
            "display_name": display_name,
            "now_playing": {
                "three_line": {"line1": line1, "line2": line2, "line3": line3},
                "length": length,
            },
            "outputs": [],
        }],
    }


class TestListeningHistoryStore(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self._t = 1_000_000.0
        self.store = ListeningHistoryStore(self.db, clock=lambda: self._t)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_records_a_new_track(self):
        self.store.handle_event(
            _state("z1", "Kitchen", "So What", "Miles Davis", "Kind of Blue", 545),
        )
        rows = self.store.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "So What")
        self.assertEqual(rows[0]["artist"], "Miles Davis")
        self.assertEqual(rows[0]["album"], "Kind of Blue")
        self.assertEqual(rows[0]["zone"], "Kitchen")
        self.assertEqual(rows[0]["duration"], 545)

    def test_same_track_not_recorded_twice(self):
        evt = _state("z1", "Kitchen", "So What")
        self.store.handle_event(evt)
        self.store.handle_event(evt)
        self.assertEqual(len(self.store.query()), 1)

    def test_new_track_after_change_is_recorded(self):
        self.store.handle_event(_state("z1", "Kitchen", "So What"))
        self._t += 200
        self.store.handle_event(_state("z1", "Kitchen", "Blue in Green"))
        titles = [r["title"] for r in self.store.query()]
        self.assertIn("So What", titles)
        self.assertIn("Blue in Green", titles)

    def test_stop_marker_is_filtered(self):
        self.store.set_stop_marker_title("Swarpius_Stop_Playback")
        self.store.handle_event(_state("z1", "Kitchen", "Swarpius_Stop_Playback"))
        self.assertEqual(self.store.query(), [])

    def test_query_filters_by_time_and_zone(self):
        self.store.handle_event(_state("z1", "Kitchen", "Track A"))
        self._t += 1000
        self.store.handle_event(_state("z2", "Bedroom", "Track B"))

        kitchen = self.store.query(zone="Kitchen")
        self.assertEqual([r["title"] for r in kitchen], ["Track A"])

        # since just after Track A → only Track B
        recent = self.store.query(since_ms=int((1_000_000.0 + 500) * 1000))
        self.assertEqual([r["title"] for r in recent], ["Track B"])

    def test_clear_empties_the_store(self):
        self.store.handle_event(_state("z1", "Kitchen", "Track A"))
        self.store.clear()
        self.assertEqual(self.store.query(), [])


if __name__ == "__main__":
    unittest.main()
