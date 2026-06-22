"""CostLedger: one row per LLM agent invocation, aggregated for the cost
dashboard. Backed by the shared StateDb (cost_ledger table, added at schema v2).
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.io.cost_ledger import CostLedger
from app.io.state_db import StateDb


class TestCostLedger(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.ledger = CostLedger(self.db)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_record_totals(self):
        self.ledger.record(agent="Coordinator", model="anthropic/opus",
                            cost_usd=0.10, input_tokens=100, output_tokens=50, ts=1000)
        self.ledger.record(agent="Analyser", model="anthropic/sonnet",
                            cost_usd=0.30, input_tokens=200, output_tokens=80, ts=2000)
        total = self.ledger.aggregate()["total"]
        self.assertAlmostEqual(total["cost_usd"], 0.40)
        self.assertEqual(total["input_tokens"], 300)
        self.assertEqual(total["count"], 2)

    def test_breakdown_by_agent_and_model(self):
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=0.10, ts=1000)
        self.ledger.record(agent="Analyser", model="m2", cost_usd=0.30, ts=2000)
        self.ledger.record(agent="Analyser", model="m1", cost_usd=0.05, ts=3000)
        agg = self.ledger.aggregate()
        by_agent = {r["key"]: r["cost_usd"] for r in agg["by_agent"]}
        self.assertAlmostEqual(by_agent["Analyser"], 0.35)
        self.assertAlmostEqual(by_agent["Coordinator"], 0.10)
        by_model = {r["key"]: r["cost_usd"] for r in agg["by_model"]}
        self.assertAlmostEqual(by_model["m1"], 0.15)
        self.assertAlmostEqual(by_model["m2"], 0.30)

    def test_filter_by_agent_restricts_all_breakdowns(self):
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=0.10, ts=1000)
        self.ledger.record(agent="Analyser", model="m2", cost_usd=0.30, ts=2000)
        agg = self.ledger.aggregate(agent="Analyser")
        self.assertAlmostEqual(agg["total"]["cost_usd"], 0.30)
        self.assertEqual([r["key"] for r in agg["by_agent"]], ["Analyser"])

    def test_filter_by_model(self):
        self.ledger.record(agent="Coordinator", model="m1", cost_usd=0.10, ts=1000)
        self.ledger.record(agent="Analyser", model="m2", cost_usd=0.30, ts=2000)
        agg = self.ledger.aggregate(model="m2")
        self.assertAlmostEqual(agg["total"]["cost_usd"], 0.30)

    def test_by_shape_buckets_by_step_count(self):
        # Mean cost per request by complexity: rows bucket by coordinator step
        # count (1-2 simple, 3-4 compound, 5+ complex). Rows without steps
        # (sub-agent/analyser) are excluded.
        self.ledger.record(agent="Coordinator", model="m", cost_usd=0.02, steps=1, ts=1000)
        self.ledger.record(agent="Coordinator", model="m", cost_usd=0.04, steps=2, ts=2000)
        self.ledger.record(agent="Coordinator", model="m", cost_usd=0.10, steps=4, ts=3000)
        self.ledger.record(agent="Coordinator", model="m", cost_usd=0.30, steps=7, ts=4000)
        self.ledger.record(agent="Analyser", model="m", cost_usd=1.00, ts=5000)  # no steps
        by_shape = {r["key"]: r for r in self.ledger.aggregate()["by_shape"]}
        self.assertEqual(set(by_shape), {"simple", "compound", "complex"})
        self.assertAlmostEqual(by_shape["simple"]["cost_usd"], 0.06)
        self.assertEqual(by_shape["simple"]["count"], 2)
        self.assertAlmostEqual(by_shape["compound"]["cost_usd"], 0.10)
        self.assertAlmostEqual(by_shape["complex"]["cost_usd"], 0.30)

    def test_time_range(self):
        self.ledger.record(agent="A", model="m", cost_usd=0.10, ts=1000)
        self.ledger.record(agent="A", model="m", cost_usd=0.20, ts=5000)
        agg = self.ledger.aggregate(since_ms=2000)
        self.assertAlmostEqual(agg["total"]["cost_usd"], 0.20)

    def test_missing_cost_defaults_to_zero(self):
        self.ledger.record(agent="Local", model="ollama/llama", cost_usd=None,
                            input_tokens=100, output_tokens=20, ts=1000)
        total = self.ledger.aggregate()["total"]
        self.assertEqual(total["cost_usd"], 0.0)
        self.assertEqual(total["count"], 1)


if __name__ == "__main__":
    unittest.main()
