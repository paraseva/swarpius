"""The CLI /usage view gains an all-time cost overview from the ledger
(total + by-agent + by-model + last-7-days), on top of the session block."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app.cli.session_usage import format_cost_overview
from app.io.cost_ledger import CostLedger, NullCostLedger, set_cost_ledger
from app.io.state_db import StateDb


class TestCostOverview(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.ledger = CostLedger(self.db)
        set_cost_ledger(self.ledger)

    def tearDown(self):
        set_cost_ledger(NullCostLedger())
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_empty_ledger_yields_no_overview(self):
        self.assertEqual(format_cost_overview(), "")

    def test_overview_has_totals_agents_and_models(self):
        now = int(time.time() * 1000)
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=0.10, ts=now)
        self.ledger.record(agent="Analyser", model="m2", cost_usd=0.30, ts=now)
        text = format_cost_overview()
        self.assertIn("all time", text)
        self.assertIn("$0.40", text)
        self.assertIn("Coordinator", text)
        self.assertIn("Analyser", text)
        self.assertIn("m2", text)

    def test_last_7_days_excludes_older_spend(self):
        now = int(time.time() * 1000)
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=1.00, ts=now)
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=5.00,
                           ts=now - 30 * 86_400_000)
        text = format_cost_overview()
        # All-time total includes both; the 7-day line only the recent one.
        self.assertIn("$6.00", text)
        self.assertIn("last 7 days", text)
        self.assertIn("$1.00", text)


if __name__ == "__main__":
    unittest.main()
