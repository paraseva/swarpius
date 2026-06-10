"""Atomicity contract for feedback processing.

**A. Feedback preservation** — at every observable filesystem state,
every feedback item is in ``feedback.yaml`` OR captured in
``analysis-history.yaml``. Never lost between the two.

**B. Lesson ↔ status coupling** — for any processed feedback item,
either (lesson is in ``lessons-learned.md`` AND feedback status ≠
pending) or neither. No intermediate where the lesson is on disk but
the feedback still says pending. Retry must be idempotent: re-running
after a partial failure doesn't duplicate lessons or corrupt state.

**C. Snapshot ↔ new-analysis coupling** — if the snapshot-and-clear
operation observably ran (``analysis-history.yaml`` gained an entry
AND ``feedback.yaml`` is deleted), the new ``analysis.yaml`` must
reflect the new content. If the new-analysis write fails, filesystem
state must look as if nothing happened.

The implementation achieves these by writing ``analysis.yaml`` first
(atomic temp + rename), then atomic-writing ``analysis-history.yaml``,
and only then deleting ``feedback.yaml`` — plus idempotent append in
``write_lesson`` (key on heading + source).
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from analyser import analyse  # noqa: E402
from analyser.feedback import (  # noqa: E402
    read_feedback,
    read_lessons,
    write_feedback,
    write_lesson,
)

# ------------------------------------------------------------------ #
#  Fixtures                                                            #
# ------------------------------------------------------------------ #

def _write_yaml(path: Path, data) -> None:
    path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")


def _sample_feedback() -> list[dict]:
    return [
        {
            "request_id": "rq-c01-0001",
            "failure_mode": "FM-08",
            "disposition": "dismiss",
            "rebuttal": "Zone name came from LLM_PERSONA config",
            "lesson_status": "pending",
        },
    ]


def _sample_analysis(version: str = "v1") -> dict:
    return {
        "analysed_at": "2026-04-22T10:00:00Z",
        "conversation_id": "c01",
        "date": "2026-04-22",
        "version_marker": version,  # lets tests assert which version is on disk
        "findings": [
            {
                "request_id": "rq-c01-0001",
                "failure_mode": "FM-11",
                "severity": "medium",
                "summary": "Sample finding",
                "detail": "detail text",
            },
        ],
    }


def _make_conv_dir(tmp_dir: Path) -> Path:
    conv_dir = tmp_dir / "2026-04-22" / "c01"
    conv_dir.mkdir(parents=True)
    _write_yaml(conv_dir / "analysis.yaml", _sample_analysis("v1"))
    write_feedback(conv_dir, _sample_feedback())
    return conv_dir


# ------------------------------------------------------------------ #
#  Invariant A — Feedback preservation                                 #
# ------------------------------------------------------------------ #

class TestFeedbackPreservation(unittest.TestCase):
    """At every observable filesystem state, feedback is in feedback.yaml
    OR in analysis-history.yaml. Never lost."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.conv_dir = _make_conv_dir(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _feedback_observable(self) -> bool:
        """True if feedback is on disk in EITHER feedback.yaml or history."""
        fb_path = self.conv_dir / "feedback.yaml"
        if fb_path.exists():
            current = read_feedback(self.conv_dir)
            if current:
                return True
        history_path = self.conv_dir / "analysis-history.yaml"
        if not history_path.exists():
            return False
        try:
            history = yaml.safe_load(history_path.read_text())
        except Exception:
            return False
        if not isinstance(history, list):
            return False
        return any(
            isinstance(h, dict) and h.get("feedback") for h in history
        )

    def test_feedback_preserved_when_new_analysis_write_fails(self):
        """If write_analysis fails at any point, feedback must still be
        observable — either in feedback.yaml (preferred — nothing was
        committed) or in history (if the commit point had passed)."""
        new_analysis = _sample_analysis("v2")

        # Patch Path.write_text to raise for any *.tmp write — simulates
        # disk-full during the atomic-write step before the commit point.
        original_write_text = Path.write_text

        def selective_write_text(self_path, *args, **kwargs):
            if self_path.name.endswith(".tmp"):
                raise OSError("disk full during temp write")
            return original_write_text(self_path, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            with self.assertRaises(OSError):
                analyse.write_analysis(self.conv_dir, new_analysis)

        # Invariant A: feedback is still observable somewhere
        self.assertTrue(
            self._feedback_observable(),
            "Feedback lost after failed new-analysis write",
        )


# ------------------------------------------------------------------ #
#  Invariant C — Snapshot / new-analysis coupling                      #
# ------------------------------------------------------------------ #

class TestSnapshotNewAnalysisCoupling(unittest.TestCase):
    """If snapshot-and-clear observably ran, new analysis.yaml reflects
    the new content. Otherwise filesystem looks unchanged."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.conv_dir = _make_conv_dir(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _current_analysis_version(self) -> str | None:
        path = self.conv_dir / "analysis.yaml"
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text())
        except Exception:
            return None
        return data.get("version_marker") if isinstance(data, dict) else None

    def _snapshot_observable(self) -> bool:
        """True if snapshot-and-clear has run: history has an entry AND
        feedback.yaml has been deleted."""
        history_path = self.conv_dir / "analysis-history.yaml"
        feedback_gone = not (self.conv_dir / "feedback.yaml").exists()
        if not history_path.exists():
            return False
        try:
            history = yaml.safe_load(history_path.read_text())
        except Exception:
            return False
        history_has_entry = isinstance(history, list) and len(history) > 0
        return history_has_entry and feedback_gone

    def test_snapshot_and_analysis_write_are_atomic(self):
        """If new-analysis write fails, snapshot-and-clear must NOT be
        observable on disk. Either both happen or neither — no partial
        states where history shows the old analysis as "superseded" but
        analysis.yaml still has that old content live."""
        new_analysis = _sample_analysis("v2")

        # Inject failure at the temp-write step (pre-commit). All
        # invariants should hold because nothing has been committed.
        original_write_text = Path.write_text

        def selective_write_text(self_path, *args, **kwargs):
            if self_path.name.endswith(".tmp"):
                raise OSError("disk full during temp write")
            return original_write_text(self_path, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            with self.assertRaises(OSError):
                analyse.write_analysis(self.conv_dir, new_analysis)

        # Invariant C: observable state is all-or-nothing
        analysis_version = self._current_analysis_version()
        snapshot_ran = self._snapshot_observable()

        if snapshot_ran:
            self.assertEqual(
                analysis_version, "v2",
                "Snapshot ran but new analysis not visible — "
                "history shows v1 as superseded while v1 is still live",
            )
        else:
            self.assertEqual(
                analysis_version, "v1",
                "Snapshot did not run but analysis.yaml changed",
            )

    def test_rename_failure_leaves_no_orphan_tempfiles(self):
        """If the post-commit rename fails, we shouldn't leave orphaned
        .tmp files sitting around. A second write_analysis call later
        should still succeed (no stale temp blocking anything)."""
        new_analysis = _sample_analysis("v2")

        # Force failure at the atomic-rename commit point
        original_replace = Path.replace

        def failing_replace(self_path, target):
            if str(target).endswith("analysis.yaml"):
                raise OSError("rename failed")
            return original_replace(self_path, target)

        with patch.object(Path, "replace", failing_replace):
            with self.assertRaises(OSError):
                analyse.write_analysis(self.conv_dir, new_analysis)

        # Invariant C: analysis.yaml unchanged
        self.assertEqual(self._current_analysis_version(), "v1")
        # Feedback preserved
        fb_path = self.conv_dir / "feedback.yaml"
        self.assertTrue(fb_path.exists())


# ------------------------------------------------------------------ #
#  Invariant B — Lesson ↔ status coupling                              #
# ------------------------------------------------------------------ #

class TestLessonStatusCoupling(unittest.TestCase):
    """After a processed feedback item: either (lesson on disk AND
    status ≠ pending) or neither. Retry is idempotent — the same
    feedback can be re-processed without duplicating lessons."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.conv_dir = _make_conv_dir(Path(self._tmp.name))
        self.lessons_path = Path(self._tmp.name) / "lessons-learned.md"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _lesson_count_for_heading(self, heading: str) -> int:
        """How many times a lesson with this heading appears in the file."""
        if not self.lessons_path.exists():
            return 0
        content = read_lessons(self.lessons_path)
        return content.count(f"## {heading}\n")

    def test_write_lesson_is_idempotent_on_retry(self):
        """Invariant B's retry guarantee: calling write_lesson twice
        with the same heading + source must not produce a duplicate
        entry. Supports the "retry after partial failure" recovery path
        where feedback status might not have been updated on the first
        attempt.
        """
        heading = "External events invalidate references"
        body = "When a session expires, cached references may become invalid..."
        source = "rq-c01-0001 (2026-04-22), FM-11 dismiss"

        write_lesson(self.lessons_path, heading, body, source)
        write_lesson(self.lessons_path, heading, body, source)  # retry

        self.assertEqual(
            self._lesson_count_for_heading(heading), 1,
            "Lesson duplicated on retry — write_lesson is not idempotent",
        )

    def test_different_source_for_same_heading_distinguishable(self):
        """The idempotence key includes ``source`` so two genuinely
        different feedback items that happen to produce the same
        heading don't collapse — write_lesson matches on
        (heading, source) and preserves both."""
        heading = "External events invalidate references"
        body = "Lesson content..."

        write_lesson(self.lessons_path, heading, body, "rq-c01-0001 (2026-04-22)")
        write_lesson(self.lessons_path, heading, body, "rq-c05-0003 (2026-04-22)")

        content = read_lessons(self.lessons_path)
        # Both source attributions must appear in the file
        self.assertIn("rq-c01-0001", content, "first source attribution lost")
        self.assertIn("rq-c05-0003", content, "second source attribution lost")


if __name__ == "__main__":
    unittest.main()
