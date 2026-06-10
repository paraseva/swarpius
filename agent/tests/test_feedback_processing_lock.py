"""TDD red: tests for the per-conversation feedback lock.

New state in the feedback-item lifecycle:

    pending ─► processing ─► validated / best_effort / error / orphaned

The analyser transitions pending → processing as its first on-disk
action when it starts work on the item, and to a final state when
done. While *any* item on a conversation is pending or processing:

- submit_feedback for a *different* identity is rejected (single
  dispute per conversation — cross-finding impact means concurrent
  disputes aren't coherent).
- submit_feedback for the *same* identity is allowed when pending
  (refinement), rejected when processing (locked).
- delete_feedback for a pending item is allowed (cancel).
- delete_feedback for a processing item is rejected (locked).

Crash recovery: if a previous analyser run died mid-processing, items
stuck in "processing" are reset to "pending" at the start of the next
process_all_pending_feedback call.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from analyser import analyse  # noqa: E402
from app.analysis.feedback import (
    FEEDBACK_FILENAME,
    delete_feedback,
    submit_feedback,
)

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
        yaml.dump({"topic": "test", "findings": findings}, sort_keys=False),
        encoding="utf-8",
    )


def _load_feedback(conv_dir: Path) -> list[dict]:
    path = conv_dir / FEEDBACK_FILENAME
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _write_feedback(conv_dir: Path, items: list[dict]) -> None:
    (conv_dir / FEEDBACK_FILENAME).write_text(
        yaml.dump(items, sort_keys=False), encoding="utf-8",
    )


def _item(
    request_id: str = "rq-c01-0001",
    failure_mode: str = "FM-08",
    status: str = "pending",
    disposition: str = "dismiss",
) -> dict:
    return {
        "request_id": request_id,
        "failure_mode": failure_mode,
        "disposition": disposition,
        "rebuttal": "x",
        "timestamp": "2026-04-22T12:00:00+00:00",
        "lesson_status": status,
        "validation_iterations": 0,
    }


# ---------------------------------------------------------------------------
# submit_feedback — per-conversation lock
# ---------------------------------------------------------------------------


class TestSubmitLock(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)
        self.date = "2026-04-22"
        self.conv_id = "c01"
        self.conv_dir = self.logs_root / self.date / self.conv_id
        _write_analysis(self.conv_dir, SAMPLE_FINDINGS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_other_identity_pending_blocks_submit(self):
        """While F1 is pending, disputing F2 is rejected — disputes
        cascade (one lesson can eliminate both findings) so concurrent
        disputes on different findings aren't coherent."""
        _write_feedback(self.conv_dir, [_item("rq-c01-0001", "FM-08")])
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0002", failure_mode="FM-12",
            disposition="downgrade", rebuttal="second",
        )
        self.assertFalse(result["ok"])
        self.assertIn("pending", result["error"].lower())

    def test_other_identity_processing_blocks_submit(self):
        """Processing (analyser is actively working) is an even harder
        lock than pending — operator must wait for it to finish."""
        _write_feedback(self.conv_dir, [_item("rq-c01-0001", "FM-08", "processing")])
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0002", failure_mode="FM-12",
            disposition="downgrade", rebuttal="second",
        )
        self.assertFalse(result["ok"])
        self.assertIn("progress", result["error"].lower())

    def test_same_identity_pending_allows_refinement(self):
        """Operator changed their mind → replace the pending entry."""
        _write_feedback(self.conv_dir, [
            _item("rq-c01-0001", "FM-08", "pending", "dismiss"),
        ])
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="downgrade", rebuttal="actually downgrade",
        )
        self.assertTrue(result["ok"])
        items = _load_feedback(self.conv_dir)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["disposition"], "downgrade")

    def test_same_identity_processing_rejects(self):
        """Once the analyser is working on it, it's locked even for the
        same identity — editing would race with the ongoing LLM call."""
        _write_feedback(self.conv_dir, [_item("rq-c01-0001", "FM-08", "processing")])
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="downgrade", rebuttal="too late",
        )
        self.assertFalse(result["ok"])
        self.assertIn("progress", result["error"].lower())

    def test_resolved_other_identities_dont_lock(self):
        """Already-processed entries (validated/best_effort/error/orphaned)
        don't block new disputes on other findings — they're done."""
        _write_feedback(self.conv_dir, [
            _item("rq-c01-0001", "FM-08", "validated"),
        ])
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0002", failure_mode="FM-12",
            disposition="dismiss", rebuttal="second",
        )
        self.assertTrue(result["ok"])

    def test_same_identity_error_allows_retry(self):
        """If lesson extraction previously errored, re-disputing the
        same identity replaces the error with a fresh pending — retry
        path."""
        _write_feedback(self.conv_dir, [
            _item("rq-c01-0001", "FM-08", "error"),
        ])
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="retry",
        )
        self.assertTrue(result["ok"])
        items = _load_feedback(self.conv_dir)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["lesson_status"], "pending")


# ---------------------------------------------------------------------------
# delete_feedback — cancel a pending dispute
# ---------------------------------------------------------------------------


class TestDeleteFeedback(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)
        self.date = "2026-04-22"
        self.conv_id = "c01"
        self.conv_dir = self.logs_root / self.date / self.conv_id
        _write_analysis(self.conv_dir, SAMPLE_FINDINGS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_deletes_pending_entry(self):
        _write_feedback(self.conv_dir, [
            _item("rq-c01-0001", "FM-08", "pending"),
            _item("rq-c01-0002", "FM-12", "validated"),
        ])
        result = delete_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
        )
        self.assertTrue(result["ok"])
        items = _load_feedback(self.conv_dir)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["failure_mode"], "FM-12")

    def test_rejects_processing_entry(self):
        _write_feedback(self.conv_dir, [
            _item("rq-c01-0001", "FM-08", "processing"),
        ])
        result = delete_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
        )
        self.assertFalse(result["ok"])
        self.assertIn("progress", result["error"].lower())
        items = _load_feedback(self.conv_dir)
        self.assertEqual(len(items), 1)  # not deleted

    def test_nonexistent_entry_rejects(self):
        result = delete_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
        )
        self.assertFalse(result["ok"])

    def test_missing_conversation_rejects(self):
        result = delete_feedback(
            self.logs_root, self.date, "c99",
            request_id="rq-c01-0001", failure_mode="FM-08",
        )
        self.assertFalse(result["ok"])


# ---------------------------------------------------------------------------
# process_feedback — "processing" state transitions
# ---------------------------------------------------------------------------


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


class TestProcessingStateTransitions(unittest.TestCase):

    def test_transitions_to_processing_before_llm_work(self):
        """Before the LLM call, the item's status is committed to
        'processing' with a timestamp — so a browser refreshing at
        this point sees the locked state."""
        install_temp_lessons_path(self)
        captured_state: list[dict] = []

        def capture_state(*args, **kwargs):
            # Sampled when _extract_lesson is called — by this point
            # the 'processing' transition must already be on disk.
            captured_state.append(
                yaml.safe_load((conv / FEEDBACK_FILENAME).read_text()),
            )
            return None  # Force error path

        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_item("rq-c01-0001", "FM-08", "pending", "dismiss")])

            with patch.object(analyse, "_extract_lesson", side_effect=capture_state), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

        self.assertEqual(len(captured_state), 1)
        self.assertEqual(captured_state[0][0]["lesson_status"], "processing")
        self.assertIn("processing_started_at", captured_state[0][0])

    def test_validated_path_clears_processing_state(self):
        """After successful validation, the processing state is gone —
        feedback.yaml has been cleared by write_analysis and the
        validated entry lives in analysis-history.yaml. The key thing
        is that the operator never sees the entry stuck in
        'processing'."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_item("rq-c01-0001", "FM-08", "pending", "dismiss")])

            lesson = {"heading": "h", "body": "b", "source": "s"}
            with patch.object(analyse, "_extract_lesson", return_value=lesson), \
                 patch.object(analyse, "analyse_batch", return_value=[{"findings": []}]), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            self.assertFalse((conv / FEEDBACK_FILENAME).exists())
            history = yaml.safe_load(
                (conv / "analysis-history.yaml").read_text(encoding="utf-8"),
            )
            self.assertEqual(history[0]["feedback"][0]["lesson_status"], "validated")

    def test_error_path_clears_processing_state(self):
        """Lesson extraction failure → final state is 'error', not
        stuck on 'processing'."""
        install_temp_lessons_path(self)
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            _write_analysis(conv, SAMPLE_FINDINGS)
            _write_feedback(conv, [_item("rq-c01-0001", "FM-08", "pending", "dismiss")])

            with patch.object(analyse, "_extract_lesson", return_value=None), \
                 patch.object(analyse, "format_conversation_logs", return_value="logs"):
                analyse.process_feedback(
                    "m", "k", conv,
                    request_id="rq-c01-0001", failure_mode="FM-08",
                    guide_text="g", git_ref=None,
                )

            item = _load_feedback(conv)[0]
            self.assertEqual(item["lesson_status"], "error")


class TestCrashRecovery(unittest.TestCase):
    """Items stuck in 'processing' from a crashed run are reset at the
    start of each new scan (inside the scan lock, so no race with a
    legitimate in-progress processing)."""

    def test_stuck_processing_reset_to_pending_on_recovery_sweep(self):
        with _TempLogsRoot() as root:
            conv = root / "2026-04-22" / "c01"
            _write_analysis(conv, SAMPLE_FINDINGS)
            stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            _write_feedback(conv, [{
                **_item("rq-c01-0001", "FM-08", "processing"),
                "processing_started_at": stale_time,
            }])

            analyse.recover_stuck_processing()

            items = _load_feedback(conv)
            self.assertEqual(items[0]["lesson_status"], "pending")

    def test_fresh_processing_not_reset(self):
        """A legitimate in-progress processing (started recently) must
        not be reset — that would corrupt the in-flight run."""
        with _TempLogsRoot() as root:
            conv = root / "2026-04-22" / "c01"
            _write_analysis(conv, SAMPLE_FINDINGS)
            fresh_time = datetime.now(timezone.utc).isoformat()
            _write_feedback(conv, [{
                **_item("rq-c01-0001", "FM-08", "processing"),
                "processing_started_at": fresh_time,
            }])

            analyse.recover_stuck_processing()

            items = _load_feedback(conv)
            self.assertEqual(items[0]["lesson_status"], "processing")


if __name__ == "__main__":
    unittest.main()
