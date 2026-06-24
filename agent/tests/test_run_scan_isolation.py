"""run_scan isolation: a whole-batch failure must not sink the conversations
that would analyse fine on their own. Only the LLM boundary (llm_completion) is
mocked — real run_scan + analyse_batch are exercised.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from analyser import analyse  # noqa: E402
from analyser.llm_layer import CompletionResult  # noqa: E402


def _stale_conv(root: Path, today: str, cid: str) -> None:
    rq = root / today / cid / f"rq-{cid}-0001"
    rq.mkdir(parents=True)
    ts = (datetime.now() - timedelta(minutes=5)).isoformat()
    (rq / "request.json").write_text(
        json.dumps({"timestamp": ts, "user_input": "hi"}), encoding="utf-8",
    )


class TestRunScanIsolation(unittest.TestCase):
    def test_failed_batch_is_retried_per_conversation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = datetime.now().strftime("%Y-%m-%d")
            _stale_conv(root, today, "c01")
            _stale_conv(root, today, "c02")
            # The 2-conversation batch fails (too large combined); each
            # conversation analyses fine on its own — both must be analysed.
            side = [
                CompletionResult(text=None, error_kind="input_shape"),
                CompletionResult(text=json.dumps([{"conversation_id": "c01", "date": today}])),
                CompletionResult(text=json.dumps([{"conversation_id": "c02", "date": today}])),
            ]
            with patch.object(analyse, "LOGS_ROOT", root), \
                 patch.object(analyse, "llm_completion", side_effect=side), \
                 patch.object(analyse, "collect_metrics"):
                analysed = analyse.run_scan(
                    "anthropic/x", "key", "guide", None, 0, batch_size=5,
                )
            self.assertEqual(analysed, 2)
            self.assertTrue((root / today / "c01" / "analysis.yaml").exists())
            self.assertTrue((root / today / "c02" / "analysis.yaml").exists())


if __name__ == "__main__":
    unittest.main()
