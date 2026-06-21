"""The listening_history coordinator tool: resolves a date range to recorded
plays via the real store, filters by zone, and degrades gracefully.
"""

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.io.state_db import StateDb
from app.roon.listening_history import ListeningHistoryStore
from tools.listening_history import ListeningHistoryTool, ListeningHistoryToolConfig


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


class TestListeningHistoryTool(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        # A fixed local time so the tool's date parsing lines up with the
        # recorded epoch deterministically.
        self._played_at = datetime(2026, 6, 16, 10, 0)
        self.store = ListeningHistoryStore(
            self.db, clock=lambda: self._played_at.timestamp(),
        )
        self.tool = ListeningHistoryTool(
            ListeningHistoryToolConfig(get_listening_history=lambda: self.store),
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _run(self, **kwargs):
        params = self.tool.input_schema(**kwargs)
        return self.tool.run(params)

    def test_returns_plays_within_a_day(self):
        self.store.handle_event(_state("z1", "Kitchen", "So What"))
        out = self._run(since="2026-06-16", until="2026-06-16")
        self.assertEqual(out.count, 1)
        self.assertEqual(out.plays[0].title, "So What")
        self.assertEqual(out.plays[0].zone, "Kitchen")

    def test_excludes_plays_outside_the_range(self):
        self.store.handle_event(_state("z1", "Kitchen", "So What"))
        out = self._run(since="2026-06-17")
        self.assertEqual(out.count, 0)

    def test_zone_filter(self):
        self.store.handle_event(_state("z1", "Kitchen", "So What"))
        out = self._run(zone="Bedroom")
        self.assertEqual(out.count, 0)

    def test_invalid_date_returns_error(self):
        out = self._run(since="not-a-date")
        self.assertIsNotNone(out.error)
        self.assertEqual(out.count, 0)

    def test_unavailable_when_no_store(self):
        tool = ListeningHistoryTool(
            ListeningHistoryToolConfig(get_listening_history=lambda: None),
        )
        out = tool.run(tool.input_schema())
        self.assertIsNotNone(out.error)


if __name__ == "__main__":
    unittest.main()
