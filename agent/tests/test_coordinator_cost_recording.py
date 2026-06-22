"""A completed request records the Coordinator's cost to the ledger, with the
model + conversation id, via the WS broadcaster's request-completed handler.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.coordinator.events import RequestCompleted
from app.io.cost_ledger import CostLedger, NullCostLedger, set_cost_ledger
from app.io.state_db import StateDb
from app.io.ws_broadcaster import WsBroadcaster


class TestCoordinatorCostRecording(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.ledger = CostLedger(self.db)
        set_cost_ledger(self.ledger)
        self.broadcaster = WsBroadcaster(ws_send_fn=lambda *a, **k: None, runtime=MagicMock())

    def tearDown(self):
        set_cost_ledger(NullCostLedger())
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _complete(self, **overrides):
        event = RequestCompleted(
            request_id=overrides.get("request_id", "rq-c01-0001"),
            emitted_at_ms=overrides.get("emitted_at_ms", 1000),
            status=overrides.get("status", "success"),
            chat_response="hi",
            total_duration_ms=10,
            total_steps=1,
            usage=overrides.get("usage", {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.25}),
            coordinator_model=overrides.get("coordinator_model", "anthropic/opus"),
        )
        self.broadcaster.handle(event)

    def test_records_coordinator_cost_with_model_and_conversation(self):
        self._complete()
        agg = self.ledger.aggregate()
        self.assertAlmostEqual(agg["total"]["cost_usd"], 0.25)
        self.assertEqual(agg["total"]["count"], 1)
        self.assertEqual(agg["by_agent"][0]["key"], "Coordinator")
        self.assertEqual(agg["by_model"][0]["key"], "anthropic/opus")

    def test_records_zero_for_no_cost_model(self):
        self._complete(usage={"input_tokens": 100, "output_tokens": 20})  # no cost_usd
        total = self.ledger.aggregate()["total"]
        self.assertEqual(total["cost_usd"], 0.0)
        self.assertEqual(total["count"], 1)


if __name__ == "__main__":
    unittest.main()
