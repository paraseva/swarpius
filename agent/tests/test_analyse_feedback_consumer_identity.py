"""TDD red: tests for the identity-based feedback consumer contract.

Replaces positional finding_index with (request_id, failure_mode)
identity across find_pending_feedback and process_feedback in
passive-analyser/analyse.py. These tests will fail against current
code — they drive the Stage 2 implementation.

Contract being pinned:

- find_pending_feedback returns list[tuple[Path, str, str]] —
  (conv_dir, request_id, failure_mode).
- process_feedback signature: (model, api_key, conv_dir, request_id,
  failure_mode, guide_text, git_ref). Finds the original finding by
  identity scan of analysis.yaml, not by index.
- Orphan handling: when feedback's (request_id, failure_mode) has no
  match in the current analysis.yaml findings, mark lesson_status
  'orphaned', log a warning, return ok=True with lesson_status
  'orphaned' (no LLM call). This handles the partial-failure-in-
  write_analysis case where feedback.yaml survives a supersede.

LLM calls (_extract_lesson, _refine_lesson) and re-analysis
(analyse_batch) are patched out; write_lesson runs against a temp
LESSONS_PATH so lessons-learned.md write is exercised for real.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from analyser import analyse  # noqa: E402
from analyser.feedback import FEEDBACK_FILENAME  # noqa: E402

try:
    from tests._analyser_fixtures import install_temp_lessons_path
except ModuleNotFoundError:
    from _analyser_fixtures import install_temp_lessons_path

SAMPLE_FINDINGS = [
    {
        "request_id": "rq-c01-0001",
        "failure_mode": "FM-08",
        "failure_name": "Failed context reference",
        "severity": "medium",
        "summary": "Used stale reference.",
    },
    {
        "request_id": "rq-c01-0002",
        "failure_mode": "FM-12",
        "failure_name": "Premature answer",
        "severity": "low",
        "summary": "Answered before search done.",
    },
]


def _write_analysis(conv_dir: Path, findings: list[dict]) -> None:
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "analysis.yaml").write_text(
        yaml.dump(
            {"git_ref": "abc", "conversation_id": conv_dir.name,
             "date": conv_dir.parent.name, "findings": findings},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_feedback(conv_dir: Path, items: list[dict]) -> None:
    (conv_dir / FEEDBACK_FILENAME).write_text(
        yaml.dump(items, sort_keys=False), encoding="utf-8",
    )


def _identity_item(
    request_id: str, failure_mode: str,
    disposition: str = "dismiss", status: str = "pending",
) -> dict:
    return {
        "request_id": request_id,
        "failure_mode": failure_mode,
        "disposition": disposition,
        "rebuttal": f"r for {request_id}",
        "timestamp": "2026-04-22T12:00:00",
        "lesson_status": status,
        "validation_iterations": 0,
    }


class _TempLogsRoot:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self._patch = patch.object(analyse, "LOGS_ROOT", self.path)
        self._patch.start()
        return self.path

    def __exit__(self, *exc):
        self._patch.stop()
        self._tmp.cleanup()


class TestFindPendingFeedbackIdentity(unittest.TestCase):
    """find_pending_feedback returns (conv_dir, request_id, failure_mode) tuples."""

    def test_empty_logs_root_returns_empty(self):
        with _TempLogsRoot():
            self.assertEqual(analyse.find_pending_feedback(), [])

    def test_conversation_without_feedback_file_skipped(self):
        with _TempLogsRoot() as root:
            conv = root / "2026-04-22" / "c01"
            _write_analysis(conv, SAMPLE_FINDINGS)  # no feedback.yaml
            self.assertEqual(analyse.find_pending_feedback(), [])

    def test_pending_items_returned_with_identity(self):
        with _TempLogsRoot() as root:
            conv = root / "2026-04-22" / "c01"
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [
                _identity_item("rq-c01-0001", "FM-08"),
                _identity_item("rq-c01-0002", "FM-12", "downgrade"),
            ])

            pending = analyse.find_pending_feedback()
            self.assertEqual(pending, [
                (conv, "rq-c01-0001", "FM-08"),
                (conv, "rq-c01-0002", "FM-12"),
            ])

    def test_collects_across_multiple_conversations_and_dates(self):
        with _TempLogsRoot() as root:
            conv_a = root / "2026-04-22" / "c01"
            conv_b = root / "2026-04-22" / "c02"
            conv_c = root / "2026-04-21" / "c05"
            for c in (conv_a, conv_b, conv_c):
                _write_analysis(c, SAMPLE_FINDINGS)
            _write_feedback(conv_a, [_identity_item("rq-c01-0001", "FM-08")])
            _write_feedback(conv_b, [_identity_item("rq-c01-0002", "FM-12")])
            _write_feedback(conv_c, [_identity_item("rq-c01-0001", "FM-08")])

            pending = analyse.find_pending_feedback()
            self.assertEqual(len(pending), 3)
            self.assertIn((conv_a, "rq-c01-0001", "FM-08"), pending)
            self.assertIn((conv_b, "rq-c01-0002", "FM-12"), pending)
            self.assertIn((conv_c, "rq-c01-0001", "FM-08"), pending)

    def test_processed_items_skipped(self):
        with _TempLogsRoot() as root:
            conv = root / "2026-04-22" / "c01"
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [
                _identity_item("rq-c01-0001", "FM-08", status="validated"),
                _identity_item("rq-c01-0002", "FM-12", status="best_effort"),
                _identity_item("rq-c01-0001", "FM-08"),  # shouldn't happen post-fix but pins the filter
            ])

            pending = analyse.find_pending_feedback()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1:], ("rq-c01-0001", "FM-08"))

    def test_orphaned_items_skipped(self):
        """Items with lesson_status 'orphaned' aren't pending — they
        won't be retried because the finding is gone."""
        with _TempLogsRoot() as root:
            conv = root / "2026-04-22" / "c01"
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [
                _identity_item("rq-c01-0001", "FM-08", status="orphaned"),
            ])

            self.assertEqual(analyse.find_pending_feedback(), [])


class TestProcessFeedbackIdentity(unittest.TestCase):
    """process_feedback takes identity, finds original finding by scan."""

    def test_identity_resolves_to_original_finding(self):
        """The finding is identified by (request_id, failure_mode)
        regardless of its ordinal position in the findings list. After
        successful validation feedback.yaml has been cleared, so the
        validated state is read back from analysis-history.yaml."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0002", "FM-12", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[{"findings": []}]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0002", failure_mode="FM-12",
                    guide_text="g", git_ref=None,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["lesson_status"], "validated")
            history = yaml.safe_load(
                (conv / "analysis-history.yaml").read_text(encoding="utf-8"),
            )
            snapshot_fb = history[0]["feedback"][0]
            self.assertEqual(snapshot_fb["request_id"], "rq-c01-0002")
            self.assertEqual(snapshot_fb["lesson_status"], "validated")

    def test_dismiss_happy_path_uses_identity_lookup(self):
        """process_feedback does an identity scan, then runs the normal
        lesson-extraction + validation flow."""
        lessons_path = install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "src"}
            reanalysis = {"findings": []}  # dismiss validated

            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[reanalysis]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["lesson_status"], "validated")
            self.assertIn("## h", lessons_path.read_text())

    def test_orphaned_identity_marks_orphaned_and_skips_processing(self):
        """When the feedback's identity isn't in current findings —
        (partial failure in write_analysis left a stale feedback.yaml).
        Skip with lesson_status=orphaned, no LLM calls, log a warning."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            # Feedback for a finding that no longer exists
            _write_feedback(conv, [_identity_item("rq-c09-0099", "FM-99", "dismiss")])

            with patch.object(analyse, "_extract_lesson") as ext, \
                 patch.object(analyse, "analyse_batch") as batch:
                with self.assertLogs(logger="analyse", level=logging.WARNING) as logs:
                    result = analyse.process_feedback(
                        "m", "k", conv,
                        request_id="rq-c09-0099", failure_mode="FM-99",
                        guide_text="g", git_ref=None,
                    )
                ext.assert_not_called()
                batch.assert_not_called()

            self.assertTrue(result["ok"])
            self.assertEqual(result["lesson_status"], "orphaned")
            self.assertTrue(any("orphan" in m.lower() for m in logs.output))

            # Persisted with orphaned status so it won't re-surface
            item = yaml.safe_load((conv / FEEDBACK_FILENAME).read_text())[0]
            self.assertEqual(item["lesson_status"], "orphaned")

    def test_missing_feedback_for_identity_returns_error(self):
        """process_feedback was called with an identity that has no
        corresponding entry in feedback.yaml — something's wrong with
        the caller. Error out, don't silently succeed."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08")])
            # Call with a different identity than what's in feedback.yaml
            result = analyse.process_feedback(
                "m", "k", conv,
                request_id="rq-c01-0002", failure_mode="FM-12",
                guide_text="g", git_ref=None,
            )
            self.assertFalse(result["ok"])

    def test_already_validated_item_is_skipped(self):
        """Identity lookup finds the item but it's already validated —
        short-circuit with already_processed."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [
                _identity_item("rq-c01-0001", "FM-08", status="validated"),
            ])

            with patch.object(analyse, "_extract_lesson") as ext:
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )
                ext.assert_not_called()

            self.assertEqual(result["lesson_status"], "validated")
            self.assertTrue(result.get("already_processed"))

    def test_missing_analysis_yaml_returns_error(self):
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08")])
            result = analyse.process_feedback(
                "m", "k", conv,
                request_id="rq-c01-0001", failure_mode="FM-08",
                guide_text="g", git_ref=None,
            )
            self.assertFalse(result["ok"])
            self.assertIn("analysis.yaml", result["error"])

    def test_dismiss_lesson_extraction_failure_marks_error(self):
        """_extract_lesson returning None short-circuits before the
        validation loop, marking the entry 'error'."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])

            with patch.object(analyse, "_extract_lesson", return_value=None), \
                 patch.object(analyse, "analyse_batch") as batch, \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )
                batch.assert_not_called()

            self.assertFalse(result["ok"])
            item = yaml.safe_load((conv / FEEDBACK_FILENAME).read_text())[0]
            self.assertEqual(item["lesson_status"], "error")

    def test_dismiss_validation_exhausted_marks_best_effort(self):
        """Re-analysis keeps producing the finding — exhaust iterations
        → best_effort."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            stuck = {"findings": [SAMPLE_FINDINGS[0]]}

            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[stuck]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["lesson_status"], "best_effort")
            self.assertEqual(result["validation_iterations"], 1)


class TestProcessAllPendingFeedbackIdentity(unittest.TestCase):
    """Orchestrator passes identity tuples through to process_feedback."""

    def test_returns_zero_when_no_pending(self):
        with _TempLogsRoot():
            self.assertEqual(
                analyse.process_all_pending_feedback("m", "k", "g", None), 0,
            )

    def test_processes_pending_by_identity(self):
        """The orchestrator iterates pending items across conversations.
        Production has at most one pending item per conv (per-conv
        lock), so spread across two convs to exercise the loop without
        running into the "validate one → write_analysis clears
        feedback.yaml → second item lost" interaction."""
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            conv_a = root / "2026-04-22" / "c01"
            conv_b = root / "2026-04-22" / "c02"
            _write_analysis(conv_a, SAMPLE_FINDINGS)
            _write_analysis(conv_b, SAMPLE_FINDINGS)
            _write_feedback(conv_a, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])
            _write_feedback(conv_b, [_identity_item("rq-c01-0002", "FM-12", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[{"findings": []}]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                count = analyse.process_all_pending_feedback("m", "k", "g", None)

            self.assertEqual(count, 2)

    def test_continues_on_per_item_failure(self):
        """One item is orphaned (identity not in current findings),
        another processes normally — loop doesn't abort. Items live on
        separate conversations because per-conv lock allows at most one
        pending item per conv in production."""
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            conv_a = root / "2026-04-22" / "c01"
            conv_b = root / "2026-04-22" / "c02"
            _write_analysis(conv_a, SAMPLE_FINDINGS)
            _write_analysis(conv_b, SAMPLE_FINDINGS)
            _write_feedback(conv_a, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])
            _write_feedback(conv_b, [_identity_item("rq-c99-9999", "FM-99", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[{"findings": []}]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                count = analyse.process_all_pending_feedback("m", "k", "g", None)

            # dismiss → validated (counts); orphan → orphaned (still
            # ok). The key assertion is that the loop didn't abort.
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
