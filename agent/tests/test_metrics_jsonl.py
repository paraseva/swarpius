"""Tests for metrics.jsonl enrichment with per-conversation usage/cost.

The metrics collector must walk each conversation's rq-*/outcome.json and
sum the usage fields (input_tokens, output_tokens, total_tokens, cache
read/creation, cost_usd) into a `usage` block on the metrics entry. This
lets the metrics page render token-and-cost trends over time.
"""

import argparse
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# passive-analyser/ is outside agent/, so add it to sys.path
PASSIVE_ANALYSIS_DIR = Path(__file__).resolve().parent.parent.parent / "passive-analyser"
if str(PASSIVE_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(PASSIVE_ANALYSIS_DIR))

from analyser.metrics import _aggregate_conversation_usage  # noqa: E402


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_outcome(cost_usd: float | None = None, *, completed: bool = True,
                  input_tokens: int = 1000, output_tokens: int = 200,
                  cache_read: int = 0, cache_creation: int = 0) -> dict:
    outcome = {
        "request_id": "rq-c01-0001",
        "status": "completed" if completed else "interrupted",
        "total_steps": 2,
    }
    usage: dict = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }
    if cost_usd is not None:
        usage["cost_usd"] = cost_usd
    outcome["usage"] = usage
    return outcome


class TestAggregateConversationUsage(unittest.TestCase):

    def _make_conv(self, tmp: Path) -> Path:
        conv = tmp / "2026-04-17" / "c01"
        conv.mkdir(parents=True)
        return conv

    def test_sums_usage_across_requests(self):
        with TemporaryDirectory() as tmp:
            conv = self._make_conv(Path(tmp))
            _write_json(conv / "rq-c01-0001" / "outcome.json",
                        _make_outcome(cost_usd=0.010, input_tokens=1000,
                                      output_tokens=200, cache_read=500))
            _write_json(conv / "rq-c01-0002" / "outcome.json",
                        _make_outcome(cost_usd=0.020, input_tokens=2000,
                                      output_tokens=400, cache_creation=300))

            usage = _aggregate_conversation_usage(conv)

            self.assertEqual(usage["input_tokens"], 3000)
            self.assertEqual(usage["output_tokens"], 600)
            self.assertEqual(usage["total_tokens"], 3600)
            self.assertEqual(usage["cache_read_input_tokens"], 500)
            self.assertEqual(usage["cache_creation_input_tokens"], 300)
            self.assertAlmostEqual(usage["cost_usd"], 0.030, places=6)

    def test_returns_zeros_when_no_outcomes(self):
        with TemporaryDirectory() as tmp:
            conv = self._make_conv(Path(tmp))
            usage = _aggregate_conversation_usage(conv)
            self.assertEqual(usage["input_tokens"], 0)
            self.assertEqual(usage["cost_usd"], 0.0)

    def test_handles_missing_usage_block_gracefully(self):
        """Old outcome.json files without a usage block contribute zero
        rather than raising.
        """
        with TemporaryDirectory() as tmp:
            conv = self._make_conv(Path(tmp))
            # First request has usage, second doesn't
            _write_json(conv / "rq-c01-0001" / "outcome.json",
                        _make_outcome(cost_usd=0.005))
            _write_json(conv / "rq-c01-0002" / "outcome.json", {
                "request_id": "rq-c01-0002",
                "status": "completed",
                "total_steps": 1,
                # no usage block
            })
            usage = _aggregate_conversation_usage(conv)
            self.assertEqual(usage["input_tokens"], 1000)
            self.assertAlmostEqual(usage["cost_usd"], 0.005, places=6)

    def test_missing_cost_usd_treated_as_zero(self):
        """Usage blocks predating cost tracking just contribute 0 to cost."""
        with TemporaryDirectory() as tmp:
            conv = self._make_conv(Path(tmp))
            _write_json(conv / "rq-c01-0001" / "outcome.json",
                        _make_outcome(cost_usd=None))
            usage = _aggregate_conversation_usage(conv)
            self.assertEqual(usage["input_tokens"], 1000)
            self.assertEqual(usage["cost_usd"], 0.0)

    def test_ignores_non_rq_directories(self):
        with TemporaryDirectory() as tmp:
            conv = self._make_conv(Path(tmp))
            _write_json(conv / "rq-c01-0001" / "outcome.json",
                        _make_outcome(cost_usd=0.01))
            # Noise: conversation_summary.json, analysis.yaml at conv level
            (conv / "random-dir").mkdir()
            _write_json(conv / "random-dir" / "outcome.json",
                        _make_outcome(cost_usd=99.0))
            usage = _aggregate_conversation_usage(conv)
            self.assertAlmostEqual(usage["cost_usd"], 0.01, places=6)

    def test_skips_unreadable_outcome_files(self):
        with TemporaryDirectory() as tmp:
            conv = self._make_conv(Path(tmp))
            _write_json(conv / "rq-c01-0001" / "outcome.json",
                        _make_outcome(cost_usd=0.01))
            (conv / "rq-c01-0002").mkdir()
            (conv / "rq-c01-0002" / "outcome.json").write_text("{invalid", encoding="utf-8")
            usage = _aggregate_conversation_usage(conv)
            self.assertAlmostEqual(usage["cost_usd"], 0.01, places=6)


class TestExtractMetricsLineIncludesUsage(unittest.TestCase):
    """The metrics JSONL line must include the per-conversation usage block
    when conv_dir is provided.
    """

    def test_usage_appears_in_serialised_line(self):
        from analyser.metrics import extract_metrics_line

        with TemporaryDirectory() as tmp:
            conv = Path(tmp) / "2026-04-17" / "c01"
            conv.mkdir(parents=True)
            _write_json(conv / "rq-c01-0001" / "outcome.json",
                        _make_outcome(cost_usd=0.015, input_tokens=2000))

            analysis = {
                "analysed_at": "2026-04-17T10:00:00Z",
                "git_ref": "abc1234",
                "conversation_id": "c01",
                "date": "2026-04-17",
                "requests_analysed": 1,
                "total_steps": 2,
                "avg_steps_per_request": 2.0,
                "findings": [],
            }
            line = extract_metrics_line(analysis, model="anthropic/x", conv_dir=conv)
            entry = json.loads(line)
            self.assertIn("usage", entry)
            self.assertEqual(entry["usage"]["input_tokens"], 2000)
            self.assertAlmostEqual(entry["usage"]["cost_usd"], 0.015, places=6)

    def test_usage_defaults_to_zeros_when_conv_dir_missing(self):
        """Called without conv_dir, the legacy shape still works (empty
        usage block rather than raising).
        """
        from analyser.metrics import extract_metrics_line

        analysis = {
            "analysed_at": "2026-04-17T10:00:00Z",
            "conversation_id": "c01",
            "date": "2026-04-17",
            "findings": [],
        }
        line = extract_metrics_line(analysis, model=None)
        entry = json.loads(line)
        self.assertIn("usage", entry)
        self.assertEqual(entry["usage"]["cost_usd"], 0.0)


class TestExtractMetricsLineRevocations(unittest.TestCase):
    """Revoked findings need to appear separately in the JSONL entry so
    metrics summaries can report revocation rate and per-FM breakdown.
    Only revocations enriched with `original_finding` (i.e. ones that
    actually matched a finding) are counted — unmatched entries are
    analyser noise."""

    def _base_analysis(self) -> dict:
        return {
            "analysed_at": "2026-04-17T10:00:00Z",
            "git_ref": "abc1234",
            "conversation_id": "c01",
            "date": "2026-04-17",
            "requests_analysed": 1,
            "total_steps": 2,
            "avg_steps_per_request": 2.0,
        }

    def test_revoked_count_and_by_mode_populated(self):
        from analyser.metrics import extract_metrics_line
        analysis = self._base_analysis() | {
            "findings": [{"failure_mode": "FM-01", "severity": "low"}],
            "revoked_findings": [
                {
                    "id": "a3f9", "reason": "false positive",
                    "original_finding": {"failure_mode": "FM-11", "severity": "medium"},
                },
                {
                    "id": "1c2e", "reason": "also fine",
                    "original_finding": {"failure_mode": "FM-11", "severity": "high"},
                },
                {
                    "id": "9b4d", "reason": "ditto",
                    "original_finding": {"failure_mode": "FM-01", "severity": "low"},
                },
            ],
        }
        entry = json.loads(extract_metrics_line(analysis, model=None))
        self.assertEqual(entry["finding_count"], 1)
        self.assertEqual(entry["revoked_count"], 3)
        self.assertEqual(entry["revoked_by_mode"], {"FM-11": 2, "FM-01": 1})

    def test_unmatched_revocations_not_counted(self):
        """Revocations without `original_finding` (because the analyser
        referenced an unknown id) shouldn't inflate the revoked count."""
        from analyser.metrics import extract_metrics_line
        analysis = self._base_analysis() | {
            "findings": [],
            "revoked_findings": [
                {"id": "zzzz", "reason": "no match — should be ignored"},
            ],
        }
        entry = json.loads(extract_metrics_line(analysis, model=None))
        self.assertEqual(entry["revoked_count"], 0)
        self.assertEqual(entry["revoked_by_mode"], {})

class TestCmdCollectFreshInstall(unittest.TestCase):
    """On a fresh install the analysis/ subdir under $SWARPIUS_DATA_DIR
    does not exist until the analyser first runs. cmd_collect must
    create the parent directory rather than erroring on the missing
    path.
    """

    def test_creates_parent_dir_when_missing(self) -> None:
        from analyser import metrics

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_logs = tmp_path / "logs" / "conversation"
            fake_metrics_file = tmp_path / "analysis" / "metrics.jsonl"
            self.assertFalse(
                fake_metrics_file.parent.exists(),
                "precondition: analysis/ must not exist yet",
            )

            conv_dir = fake_logs / "2026-04-17" / "c01"
            conv_dir.mkdir(parents=True)
            (conv_dir / "analysis.yaml").write_text(
                "conversation_id: c01\n"
                "date: '2026-04-17'\n"
                "analysed_at: '2026-04-17T10:00:00Z'\n"
                "git_ref: abc1234\n"
                "requests_analysed: 1\n"
                "total_steps: 2\n"
                "avg_steps_per_request: 2.0\n"
                "findings: []\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(metrics, "METRICS_FILE", fake_metrics_file),
                mock.patch.object(metrics, "LOGS_ROOT", fake_logs),
            ):
                metrics.cmd_collect(argparse.Namespace(quiet=True))

            self.assertTrue(
                fake_metrics_file.exists(),
                "cmd_collect should create metrics.jsonl (and its parent)",
            )

    def test_existing_metrics_intact_when_replace_fails(self) -> None:
        # Atomic-write contract: if the new content is fully written to
        # the .tmp sibling but the atomic .replace() step itself fails
        # (or a kill arrives between them), the previous metrics.jsonl
        # must survive intact rather than be a half-rewritten file.
        from analyser import metrics

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_logs = tmp_path / "logs" / "conversation"
            fake_metrics_file = tmp_path / "analysis" / "metrics.jsonl"
            fake_metrics_file.parent.mkdir(parents=True)
            previous = '{"date":"2026-01-01","conversation_id":"old"}\n'
            fake_metrics_file.write_text(previous, encoding="utf-8")

            conv_dir = fake_logs / "2026-04-17" / "c01"
            conv_dir.mkdir(parents=True)
            (conv_dir / "analysis.yaml").write_text(
                "conversation_id: c01\n"
                "date: '2026-04-17'\n"
                "analysed_at: '2026-04-17T10:00:00Z'\n"
                "git_ref: abc1234\n"
                "requests_analysed: 1\n"
                "total_steps: 2\n"
                "avg_steps_per_request: 2.0\n"
                "findings: []\n",
                encoding="utf-8",
            )

            original_replace = Path.replace

            def failing_replace(self, target):
                if str(target).endswith("metrics.jsonl"):
                    raise OSError("simulated crash between write and rename")
                return original_replace(self, target)

            with (
                mock.patch.object(metrics, "METRICS_FILE", fake_metrics_file),
                mock.patch.object(metrics, "LOGS_ROOT", fake_logs),
                mock.patch.object(Path, "replace", failing_replace),
            ):
                with self.assertRaises(OSError):
                    metrics.cmd_collect(argparse.Namespace(quiet=True))

            self.assertEqual(
                fake_metrics_file.read_text(encoding="utf-8"),
                previous,
                "previous metrics.jsonl must survive a failed rewrite",
            )


if __name__ == "__main__":
    unittest.main()
