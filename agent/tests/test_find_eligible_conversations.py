"""find_eligible_conversations eligibility contract.

Pins which conversations the scan treats as eligible vs skips
(already-analysed, no readable request.json, not-yet-stale).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from analyser import analyse  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")


def _write_conv(
    root: Path, conv: str, *, request_json: bool = True, analysed: bool = False,
    skipped: bool = False,
) -> None:
    rq = root / TODAY / conv / f"rq-{conv}-0001"
    rq.mkdir(parents=True)
    if request_json:
        ts = (datetime.now() - timedelta(minutes=1)).isoformat()
        (rq / "request.json").write_text(json.dumps({"timestamp": ts}), encoding="utf-8")
    if analysed:
        (root / TODAY / conv / "analysis.yaml").write_text("ok\n", encoding="utf-8")
    if skipped:
        (root / TODAY / conv / "analysis.skipped.yaml").write_text(
            "skipped: true\n", encoding="utf-8",
        )


class TestFindEligible(unittest.TestCase):
    def _eligible(self, build) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build(root)
            with patch.object(analyse, "LOGS_ROOT", root):
                return [p.name for p in analyse.find_eligible_conversations(0)]

    def test_includes_a_fresh_unanalysed_conversation(self):
        self.assertEqual(self._eligible(lambda r: _write_conv(r, "c01")), ["c01"])

    def test_excludes_already_analysed(self):
        self.assertEqual(
            self._eligible(lambda r: _write_conv(r, "c01", analysed=True)), [],
        )

    def test_excludes_conversation_without_request_json(self):
        self.assertEqual(
            self._eligible(lambda r: _write_conv(r, "c01", request_json=False)), [],
        )

    def test_excludes_a_conversation_marked_unanalysable(self):
        """A conversation carrying a skip marker (it can never be analysed —
        e.g. too large) is not re-offered every scan."""
        self.assertEqual(
            self._eligible(lambda r: _write_conv(r, "c01", skipped=True)), [],
        )


if __name__ == "__main__":
    unittest.main()
