"""Single-conversation Re-Analyse must scope feedback processing
to the target conversation, and skip the explicit ``analyse_batch``
when feedback processing already produced a fresh analysis.yaml.

Without this scoping, Re-Analyse on a target conversation would have
two problems:

  1. Re-Analyse on c10 would burn LLM calls processing pending feedback
     on unrelated conversations (c5, c7, …) — the operator only
     asked for c10.
  2. When c10 itself has pending feedback, the validation loop in
     ``process_feedback`` already runs ``analyse_batch(c10)`` 1–3
     times and writes the result. An unconditional follow-up
     ``analyse_batch(c10)`` would be duplicate work and can clobber the
     validated analysis with a non-deterministic re-roll.

Contract being pinned: ``run_single_conversation_analysis``
  - processes pending feedback for ``conv_dir`` only
  - skips the explicit re-analysis when feedback processing already
    wrote ``analysis.yaml``
  - falls through to an explicit re-analysis when there is no
    pending feedback, or when feedback processing produced no write
    (orphaned / error / all-iterations-failed).
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


SAMPLE_FINDING_C10 = {
    "request_id": "rq-c10-0001",
    "failure_mode": "FM-08",
    "failure_name": "Failed context reference",
    "severity": "medium",
    "summary": "c10 issue.",
}

SAMPLE_FINDING_C05 = {
    "request_id": "rq-c05-0001",
    "failure_mode": "FM-12",
    "failure_name": "Premature answer",
    "severity": "low",
    "summary": "c05 issue.",
}


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


def _read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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


class _TempLogsRoot:
    """Patch ``analyse.LOGS_ROOT`` to a fresh tmp dir for the duration
    of the test."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self._patch = patch.object(analyse, "LOGS_ROOT", self.path)
        self._patch.start()
        return self.path

    def __exit__(self, *exc):
        self._patch.stop()
        self._tmp.cleanup()


class TestSingleConvScopesFeedback(unittest.TestCase):
    """Re-Analyse on c10 must NOT process pending feedback on other
    conversations."""

    def test_other_conversation_pending_feedback_is_not_processed(self):
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            date = "2026-05-01"
            c10 = root / date / "c10"
            c05 = root / date / "c05"
            _write_analysis(c10, "v1", [SAMPLE_FINDING_C10])
            _write_analysis(c05, "v1", [SAMPLE_FINDING_C05])
            _write_feedback(c05, [_pending_item("rq-c05-0001", "FM-12")])

            # Track which conversations were passed to analyse_batch.
            calls: list[Path] = []

            def fake_batch(_model, _key, conv_dirs, *_args, **_kwargs):
                calls.extend(conv_dirs)
                return [
                    {
                        "git_ref": "abc",
                        "conversation_id": d.name,
                        "date": d.parent.name,
                        "version_marker": "v2",
                        "findings": [],
                    }
                    for d in conv_dirs
                ]

            with patch.object(analyse, "analyse_batch", side_effect=fake_batch), \
                 patch.object(analyse, "_extract_lesson") as ext, \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                analyse.run_single_conversation_analysis(
                    "m", "k", c10, "g", None,
                )
                # c05's feedback would require an LLM extraction — must
                # not be touched on a c10 Re-Analyse.
                ext.assert_not_called()

            # analyse_batch should only have been called for c10.
            self.assertEqual(calls, [c10])

            # c05's feedback.yaml stays untouched.
            self.assertTrue((c05 / FEEDBACK_FILENAME).exists())
            c05_fb = _read_yaml(c05 / FEEDBACK_FILENAME)
            self.assertEqual(c05_fb[0]["lesson_status"], "pending")

            # c05's analysis.yaml stays at v1.
            self.assertEqual(_read_yaml(c05 / "analysis.yaml")["version_marker"], "v1")


class TestSingleConvSkipsRedundantAnalyseBatch(unittest.TestCase):
    """When feedback processing for the target conversation produced a
    fresh analysis.yaml, the explicit follow-up analyse_batch must be
    skipped."""

    def test_validated_feedback_does_not_trigger_extra_analyse_batch(self):
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            date = "2026-05-01"
            c10 = root / date / "c10"
            _write_analysis(c10, "v1", [SAMPLE_FINDING_C10])
            _write_feedback(c10, [_pending_item("rq-c10-0001", "FM-08")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            validated_analysis = {
                "git_ref": "abc",
                "conversation_id": "c10",
                "date": date,
                "version_marker": "v_validated",
                "findings": [],  # disputed finding gone after lesson
            }
            calls: list[Path] = []

            def fake_batch(_model, _key, conv_dirs, *_args, **_kwargs):
                calls.extend(conv_dirs)
                return [validated_analysis]

            with patch.object(analyse, "analyse_batch", side_effect=fake_batch), \
                 patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                analyse.run_single_conversation_analysis(
                    "m", "k", c10, "g", None,
                )

            # process_feedback's validation loop calls analyse_batch
            # exactly once (the first iteration validates). There must
            # be NO additional call from the single-conv wrapper —
            # i.e. analyse_batch ran once total.
            self.assertEqual(len(calls), 1)

            # The validated analysis is what's on disk.
            self.assertEqual(
                _read_yaml(c10 / "analysis.yaml")["version_marker"],
                "v_validated",
            )

    def test_no_pending_feedback_falls_through_to_analyse_batch(self):
        """No pending feedback for c10 → the explicit analyse_batch IS
        the only path that re-analyses the conversation. Must run."""
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            date = "2026-05-01"
            c10 = root / date / "c10"
            _write_analysis(c10, "v1", [SAMPLE_FINDING_C10])
            # No feedback.yaml.

            calls: list[Path] = []

            def fake_batch(_model, _key, conv_dirs, *_args, **_kwargs):
                calls.extend(conv_dirs)
                return [
                    {
                        "git_ref": "abc",
                        "conversation_id": "c10",
                        "date": date,
                        "version_marker": "v_explicit",
                        "findings": [],
                    }
                ]

            with patch.object(analyse, "analyse_batch", side_effect=fake_batch):
                analyse.run_single_conversation_analysis(
                    "m", "k", c10, "g", None,
                )

            self.assertEqual(calls, [c10])
            self.assertEqual(
                _read_yaml(c10 / "analysis.yaml")["version_marker"],
                "v_explicit",
            )

    def test_extraction_error_falls_through_to_analyse_batch(self):
        """Lesson extraction failed — feedback marked 'error' with no
        write. The user clicked Re-Analyse expecting fresh content;
        fall through to analyse_batch."""
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            date = "2026-05-01"
            c10 = root / date / "c10"
            _write_analysis(c10, "v1", [SAMPLE_FINDING_C10])
            _write_feedback(c10, [_pending_item("rq-c10-0001", "FM-08")])

            calls: list[Path] = []

            def fake_batch(_model, _key, conv_dirs, *_args, **_kwargs):
                calls.extend(conv_dirs)
                return [
                    {
                        "git_ref": "abc",
                        "conversation_id": "c10",
                        "date": date,
                        "version_marker": "v_explicit",
                        "findings": [SAMPLE_FINDING_C10],
                    }
                ]

            with patch.object(analyse, "analyse_batch", side_effect=fake_batch), \
                 patch.object(analyse, "_extract_lesson", return_value=None), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                analyse.run_single_conversation_analysis(
                    "m", "k", c10, "g", None,
                )

            # Feedback processing made no analyse_batch call (extraction
            # short-circuited). Single-conv wrapper must add one to
            # actually re-analyse.
            self.assertEqual(calls, [c10])
            self.assertEqual(
                _read_yaml(c10 / "analysis.yaml")["version_marker"],
                "v_explicit",
            )

            # The fall-through analyse_batch + write_analysis sweeps
            # feedback.yaml into history (write_analysis always does
            # this) — the error entry is preserved in the history
            # snapshot for the operator to inspect.
            history = _read_yaml(c10 / "analysis-history.yaml")
            self.assertEqual(history[0]["feedback"][0]["lesson_status"], "error")

    def test_orphaned_feedback_falls_through_to_analyse_batch(self):
        """Feedback identity has no match in current findings →
        orphaned, no write. Fall through so the user gets a fresh
        re-roll."""
        install_temp_lessons_path(self)
        with _TempLogsRoot() as root:
            date = "2026-05-01"
            c10 = root / date / "c10"
            _write_analysis(c10, "v1", [SAMPLE_FINDING_C10])
            # Feedback references a finding that doesn't exist.
            _write_feedback(c10, [_pending_item("rq-c99-9999", "FM-99")])

            calls: list[Path] = []

            def fake_batch(_model, _key, conv_dirs, *_args, **_kwargs):
                calls.extend(conv_dirs)
                return [
                    {
                        "git_ref": "abc",
                        "conversation_id": "c10",
                        "date": date,
                        "version_marker": "v_explicit",
                        "findings": [SAMPLE_FINDING_C10],
                    }
                ]

            with patch.object(analyse, "analyse_batch", side_effect=fake_batch), \
                 patch.object(analyse, "_extract_lesson") as ext:
                analyse.run_single_conversation_analysis(
                    "m", "k", c10, "g", None,
                )
                ext.assert_not_called()

            self.assertEqual(calls, [c10])
            self.assertEqual(
                _read_yaml(c10 / "analysis.yaml")["version_marker"],
                "v_explicit",
            )


if __name__ == "__main__":
    unittest.main()
