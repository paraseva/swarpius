"""process_feedback runs the validation loop exactly once.

With temperature=0 pinned for analysis, retrying the same lesson
produces the same analysis, so the validation pass runs a single time
and never refines the lesson between iterations: iterating would only
push lessons toward being more specific to force a single finding away,
rather than producing general guidance.

Contract being pinned:

  - One ``analyse_batch`` call per ``process_feedback`` invocation
    (the validation pass).
  - ``_refine_lesson`` is never invoked.
  - ``validation_iterations`` is always 1 on validated / best_effort
    paths (retained in the schema, but does not carry a "which attempt
    succeeded" meaning).
  - When ``analyse_batch`` returns no analysis, the result is
    best_effort with no write.
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


def _write_analysis(conv_dir: Path, version: str, findings: list[dict]) -> None:
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


def _pending_item(request_id: str, failure_mode: str) -> dict:
    return {
        "request_id": request_id,
        "failure_mode": failure_mode,
        "disposition": "dismiss",
        "rebuttal": f"r for {request_id}",
        "timestamp": "2026-05-01T12:00:00",
        "lesson_status": "pending",
        "validation_iterations": 0,
    }


def _read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestSinglePassValidation(unittest.TestCase):

    def test_validated_calls_analyse_batch_exactly_once(self):
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp) / "2026-05-01" / "c01"
            _write_analysis(conv, "v1", SAMPLE_FINDINGS)
            _write_feedback(conv, [_pending_item("rq-c01-0001", "FM-08")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            new_analysis = {
                "git_ref": "abc",
                "conversation_id": "c01",
                "date": "2026-05-01",
                "version_marker": "v_validated",
                "findings": [],
            }
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[new_analysis]) as batch, \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )
                self.assertEqual(batch.call_count, 1)

            self.assertEqual(result["lesson_status"], "validated")
            self.assertEqual(result["validation_iterations"], 1)

    def test_best_effort_calls_analyse_batch_exactly_once(self):
        """When the first analyse_batch doesn't resolve the finding,
        we go straight to best_effort — no second attempt, no refine."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp) / "2026-05-01" / "c01"
            _write_analysis(conv, "v1", SAMPLE_FINDINGS)
            _write_feedback(conv, [_pending_item("rq-c01-0001", "FM-08")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            still_failing = {
                "git_ref": "abc",
                "conversation_id": "c01",
                "date": "2026-05-01",
                "version_marker": "v_stuck",
                "findings": SAMPLE_FINDINGS,  # finding survives the lesson
            }
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[still_failing]) as batch, \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                result = analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )
                self.assertEqual(batch.call_count, 1)

            self.assertEqual(result["lesson_status"], "best_effort")
            self.assertEqual(result["validation_iterations"], 1)
            # The lesson-influenced analysis is still persisted (operator
            # sees what their lesson actually produced) — the lesson's
            # in lessons-learned.md and the new analysis is on disk.
            self.assertEqual(
                _read_yaml(conv / "analysis.yaml")["version_marker"],
                "v_stuck",
            )

    def test_refine_lesson_attribute_is_gone(self):
        """The single-pass loop has no use for ``_refine_lesson`` — it
        should be removed from the module so a stale reference can't
        creep back in."""
        self.assertFalse(
            hasattr(analyse, "_refine_lesson"),
            "_refine_lesson should be removed; single-pass validation "
            "doesn't need refinement.",
        )

    def test_max_validation_iterations_constant_is_gone(self):
        """The constant only existed to bound the loop. Single-pass
        means it has no purpose."""
        self.assertFalse(
            hasattr(analyse, "MAX_VALIDATION_ITERATIONS"),
            "MAX_VALIDATION_ITERATIONS should be removed with the loop.",
        )


if __name__ == "__main__":
    unittest.main()
