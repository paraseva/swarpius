"""Tests for analysis browser list_analysed_conversations metadata."""

import json
import tempfile
import unittest
from pathlib import Path

import yaml


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _add_request(conv_dir: Path, rq_num: int, *, cost_usd: float | None = None) -> None:
    """Add a request dir with request.json and outcome.json inside a conv dir."""
    conv_id = conv_dir.name  # e.g. c01
    rq_dir = conv_dir / f"rq-{conv_id}-{rq_num:04d}"
    _write_json(rq_dir / "request.json", {
        "request_id": rq_dir.name,
        "user_input": "test",
        "timestamp": f"2026-04-07T10:{rq_num:02d}:00",
    })
    outcome: dict = {
        "request_id": rq_dir.name,
        "status": "completed",
        "total_steps": 2,
        "coordinator_model": "anthropic/claude-sonnet-4-6",
    }
    if cost_usd is not None:
        outcome["usage"] = {
            "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
            "cost_usd": cost_usd,
        }
    _write_json(rq_dir / "outcome.json", outcome)


def _make_conv(
    root: Path,
    date: str,
    conv_id: str,
    *,
    findings: list | None = None,
    feedback: list | None = None,
    history: list | None = None,
) -> Path:
    conv_dir = root / date / conv_id
    analysis = {
        "topic": "test",
        "requests_analysed": 1,
        "total_steps": 3,
        "avg_steps_per_request": 3.0,
        "total_tool_calls": 2,
        "git_ref": "abc1234",
        "findings": findings or [],
        "analysed_at": "2026-04-07T10:00:00",
    }
    _write_yaml(conv_dir / "analysis.yaml", analysis)
    if feedback is not None:
        _write_yaml(conv_dir / "feedback.yaml", feedback)
    if history is not None:
        _write_yaml(conv_dir / "analysis-history.yaml", history)
    return conv_dir


class TestListMetadata(unittest.TestCase):
    """list_analysed_conversations includes feedback and revision metadata."""

    def test_no_feedback_no_history(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_conv(root, "2026-04-07", "c01")

            result = list_analysed_conversations(root)
            self.assertEqual(len(result["conversations"]), 1)
            entry = result["conversations"][0]
            self.assertEqual(entry["feedback_count"], 0)
            self.assertEqual(entry["pending_feedback"], 0)
            self.assertEqual(entry["analysis_revisions"], 1)

    def test_feedback_all_processed(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_conv(root, "2026-04-07", "c01", feedback=[
                {"request_id": "rq-c01-0001", "failure_mode": "FM-08",
                 "disposition": "dismiss",
                 "rebuttal": "FP", "lesson_status": "validated"},
                {"request_id": "rq-c01-0002", "failure_mode": "FM-12",
                 "disposition": "downgrade",
                 "rebuttal": "Real", "lesson_status": "best_effort"},
            ])

            result = list_analysed_conversations(root)
            entry = result["conversations"][0]
            self.assertEqual(entry["feedback_count"], 2)
            self.assertEqual(entry["pending_feedback"], 0)

    def test_feedback_with_pending(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_conv(root, "2026-04-07", "c01", feedback=[
                {"request_id": "rq-c01-0001", "failure_mode": "FM-08",
                 "disposition": "dismiss",
                 "rebuttal": "FP", "lesson_status": "validated"},
                {"request_id": "rq-c01-0002", "failure_mode": "FM-12",
                 "disposition": "downgrade",
                 "rebuttal": "Yes", "lesson_status": "pending"},
            ])

            result = list_analysed_conversations(root)
            entry = result["conversations"][0]
            self.assertEqual(entry["feedback_count"], 2)
            self.assertEqual(entry["pending_feedback"], 1)

    def test_analysis_revisions(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_conv(root, "2026-04-07", "c01", history=[
                {"analysed_at": "2026-04-06T10:00:00", "findings": []},
                {"analysed_at": "2026-04-06T12:00:00", "findings": []},
            ])

            result = list_analysed_conversations(root)
            entry = result["conversations"][0]
            self.assertEqual(entry["analysis_revisions"], 3)  # 2 history + 1 current

    def test_no_history_file_means_one_revision(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_conv(root, "2026-04-07", "c01")

            result = list_analysed_conversations(root)
            entry = result["conversations"][0]
            self.assertEqual(entry["analysis_revisions"], 1)


class TestListCostAggregation(unittest.TestCase):
    """List entries must aggregate cost_usd across all request outcomes so
    the conversation card can show a per-conversation cost next to other
    metrics.
    """

    def test_total_cost_sums_request_outcomes(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv_dir = _make_conv(root, "2026-04-07", "c01")
            _add_request(conv_dir, 1, cost_usd=0.0123)
            _add_request(conv_dir, 2, cost_usd=0.0456)
            _add_request(conv_dir, 3, cost_usd=0.0001)

            entry = list_analysed_conversations(root)["conversations"][0]
            self.assertAlmostEqual(entry["total_cost_usd"], 0.0580, places=6)

    def test_total_cost_is_zero_when_no_outcomes_have_cost(self):
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv_dir = _make_conv(root, "2026-04-07", "c01")
            _add_request(conv_dir, 1)  # no cost_usd
            _add_request(conv_dir, 2)

            entry = list_analysed_conversations(root)["conversations"][0]
            self.assertEqual(entry["total_cost_usd"], 0.0)

    def test_total_cost_ignores_missing_usage_gracefully(self):
        """Requests with outcome.json but no usage block contribute 0."""
        from app.analysis.browser import list_analysed_conversations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv_dir = _make_conv(root, "2026-04-07", "c01")
            _add_request(conv_dir, 1, cost_usd=0.02)
            _add_request(conv_dir, 2)  # no usage at all

            entry = list_analysed_conversations(root)["conversations"][0]
            self.assertAlmostEqual(entry["total_cost_usd"], 0.02, places=6)

    def test_total_cost_preserved_via_get_list_entry(self):
        from app.analysis.browser import get_list_entry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv_dir = _make_conv(root, "2026-04-07", "c01")
            _add_request(conv_dir, 1, cost_usd=0.005)
            _add_request(conv_dir, 2, cost_usd=0.015)

            entry = get_list_entry(root, "2026-04-07", "c01")
            self.assertIsNotNone(entry)
            self.assertAlmostEqual(entry["total_cost_usd"], 0.020, places=6)
