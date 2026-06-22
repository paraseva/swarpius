"""The cost-metrics WS handler aggregates the ledger with the request's
optional time range + agent/model filters."""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from app.io.cost_ledger import CostLedger, NullCostLedger, set_cost_ledger
from app.io.state_db import StateDb
from app.io.websocket_flow import _handle_cost_metrics


class TestCostMetricsHandler(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.ledger = CostLedger(self.db)
        set_cost_ledger(self.ledger)
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=0.10, ts=1000)
        self.ledger.record(agent="Analyser", model="m2", cost_usd=0.30, ts=5000)

    def tearDown(self):
        set_cost_ledger(NullCostLedger())
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _run(self, payload):
        return asyncio.run(_handle_cost_metrics(payload))

    def test_returns_totals_and_breakdowns(self):
        r = self._run({})
        self.assertAlmostEqual(r["total"]["cost_usd"], 0.40)
        self.assertEqual({k for k in r}, {"total", "by_agent", "by_model", "by_shape", "by_day"})

    def test_agent_filter(self):
        self.assertAlmostEqual(self._run({"agent": "Analyser"})["total"]["cost_usd"], 0.30)

    def test_time_range_parses_numbers(self):
        self.assertAlmostEqual(self._run({"since_ms": 2000})["total"]["cost_usd"], 0.30)

    def test_empty_string_filter_is_ignored(self):
        self.assertAlmostEqual(self._run({"agent": ""})["total"]["cost_usd"], 0.40)


if __name__ == "__main__":
    unittest.main()
