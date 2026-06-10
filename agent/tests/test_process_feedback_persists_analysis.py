"""process_feedback must persist the validated re-analysis to disk.

Contract: process_feedback uses the in-memory ``new_analysis``
produced by the validation loop and calls ``write_analysis`` to
persist it whenever the loop produced one — so ``analysis.yaml``
reflects the lesson-updated re-analysis and ``feedback.yaml`` is
cleared (snapshot lives in ``analysis-history.yaml``). Error and
orphaned paths leave analysis.yaml untouched because no new_analysis
was produced.
"""

from __future__ import annotations

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
]


def _write_analysis_yaml(conv_dir: Path, version: str, findings: list[dict]) -> None:
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "analysis.yaml").write_text(
        yaml.dump(
            {
                "git_ref": "abc",
                "conversation_id": conv_dir.name,
                "date": conv_dir.parent.name,
                "version_marker": version,
                "findings": findings,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_feedback(conv_dir: Path, items: list[dict]) -> None:
    (conv_dir / FEEDBACK_FILENAME).write_text(
        yaml.dump(items, sort_keys=False), encoding="utf-8",
    )


def _identity_item(
    request_id: str,
    failure_mode: str,
    disposition: str = "dismiss",
    status: str = "pending",
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


def _read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestProcessFeedbackPersistsAnalysis(unittest.TestCase):
    """process_feedback writes the new analysis when the validation loop
    produces one, regardless of validated vs best_effort outcome."""

    def test_validated_persists_new_analysis(self):
        """Successful validation → analysis.yaml on disk reflects the
        new analysis, the prior analysis is in history, feedback.yaml
        is gone (analyser is now in same end state as the manual
        ``--conversation`` path)."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            # The conversation directory must sit under a date dir so
            # write_analysis can build a sensible snapshot filename.
            date_dir = Path(tmp) / "2026-04-22"
            conv = date_dir / "c01"
            _write_analysis_yaml(conv, "v1", SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            new_analysis = {
                "git_ref": "abc",
                "conversation_id": "c01",
                "date": "2026-04-22",
                "version_marker": "v2",
                "findings": [],  # the disputed finding has gone after the lesson
            }

            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[new_analysis]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["lesson_status"], "validated")

            # New analysis.yaml on disk = the validated re-analysis
            current = _read_yaml(conv / "analysis.yaml")
            self.assertEqual(current["version_marker"], "v2")
            self.assertEqual(current["findings"], [])

            # Old analysis preserved in history with the validated feedback
            history = _read_yaml(conv / "analysis-history.yaml")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["version_marker"], "v1")
            self.assertEqual(len(history[0]["feedback"]), 1)
            self.assertEqual(
                history[0]["feedback"][0]["lesson_status"], "validated",
            )

            # feedback.yaml has been cleared by write_analysis
            self.assertFalse((conv / FEEDBACK_FILENAME).exists())

    def test_best_effort_persists_last_new_analysis(self):
        """Even when validation can't dismiss the finding, the lesson
        was still written and a re-analysis was produced — the operator
        should see the lesson-influenced analysis on disk, not the
        original."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            date_dir = Path(tmp) / "2026-04-22"
            conv = date_dir / "c01"
            _write_analysis_yaml(conv, "v1", SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            stuck_analysis = {
                "git_ref": "abc",
                "conversation_id": "c01",
                "date": "2026-04-22",
                "version_marker": "v_stuck",
                "findings": SAMPLE_FINDINGS,
            }

            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[stuck_analysis]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["lesson_status"], "best_effort")

            current = _read_yaml(conv / "analysis.yaml")
            self.assertEqual(current["version_marker"], "v_stuck")

            history = _read_yaml(conv / "analysis-history.yaml")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["version_marker"], "v1")
            self.assertEqual(
                history[0]["feedback"][0]["lesson_status"], "best_effort",
            )
            self.assertFalse((conv / FEEDBACK_FILENAME).exists())

    def test_extraction_failure_leaves_analysis_unchanged(self):
        """_extract_lesson returns None — nothing to validate, nothing
        to persist. analysis.yaml stays as it was."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            date_dir = Path(tmp) / "2026-04-22"
            conv = date_dir / "c01"
            _write_analysis_yaml(conv, "v1", SAMPLE_FINDINGS)
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
            current = _read_yaml(conv / "analysis.yaml")
            self.assertEqual(current["version_marker"], "v1")
            # Feedback survives so the next pass can retry; status: error
            fb = _read_yaml(conv / FEEDBACK_FILENAME)
            self.assertEqual(fb[0]["lesson_status"], "error")

    def test_reanalysis_failure_leaves_analysis_unchanged(self):
        """analyse_batch returns nothing — there's no new_analysis to
        persist. Mark best_effort but leave analysis.yaml alone (don't
        fabricate state from a non-existent re-analysis)."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            date_dir = Path(tmp) / "2026-04-22"
            conv = date_dir / "c01"
            _write_analysis_yaml(conv, "v1", SAMPLE_FINDINGS)
            _write_feedback(conv, [_identity_item("rq-c01-0001", "FM-08", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[None]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            self.assertEqual(result["lesson_status"], "best_effort")
            current = _read_yaml(conv / "analysis.yaml")
            self.assertEqual(current["version_marker"], "v1")
            # No history entry — nothing was superseded
            self.assertFalse((conv / "analysis-history.yaml").exists())


if __name__ == "__main__":
    unittest.main()
