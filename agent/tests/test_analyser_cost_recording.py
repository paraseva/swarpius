"""The analyser records its own LLM-call cost to the ledger, mapping the
litellm response's usage fields (prompt/completion tokens) onto the ledger.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from analyser.llm_layer import _record_analyser_cost
from app.io.cost_ledger import CostLedger, NullCostLedger, set_cost_ledger
from app.io.state_db import StateDb


class TestAnalyserCostRecording(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")
        self.ledger = CostLedger(self.db)
        set_cost_ledger(self.ledger)

    def tearDown(self):
        set_cost_ledger(NullCostLedger())
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_records_analyser_call_with_mapped_token_fields(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=400, completion_tokens=120,
            cache_read_input_tokens=50, cache_creation_input_tokens=0,
        ))
        _record_analyser_cost("anthropic/opus", response)
        agg = self.ledger.aggregate()
        self.assertEqual(agg["total"]["count"], 1)
        self.assertEqual(agg["total"]["input_tokens"], 400)
        self.assertEqual(agg["total"]["output_tokens"], 120)
        self.assertEqual(agg["total"]["cache_read_tokens"], 50)
        self.assertEqual(agg["by_agent"][0]["key"], "Analyser")
        self.assertEqual(agg["by_model"][0]["key"], "anthropic/opus")

    def test_no_usage_is_harmless(self):
        _record_analyser_cost("m", SimpleNamespace(usage=None))
        self.assertEqual(self.ledger.aggregate()["total"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
