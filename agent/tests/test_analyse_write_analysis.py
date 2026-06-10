"""Pin the atomic semantics of passive-analyser's write_analysis.

The flow is designed to be all-or-nothing from the observable filesystem's
point of view, with two explicit invariants:

  A. If the commit point (analysis.yaml rename) hasn't happened, on-disk
     state is unchanged — temp files are cleaned up on failure.
  B. If the commit point has happened, feedback.yaml may still be present
     (the delete step is last). That's tolerated by design: the next
     scan re-processes it, and lesson writes are idempotent on
     (heading, source), so replay doesn't duplicate.

These tests pin the happy path plus the rotation, git_ref preservation,
and cleanup-on-failure behaviours ahead of the feedback identity refactor.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from analyser import analyse  # noqa: E402
from analyser.feedback import FEEDBACK_FILENAME  # noqa: E402


def _feedback_item(
    request_id: str = "rq-c01-0001",
    failure_mode: str = "FM-08",
    status: str = "validated",
) -> dict:
    return {
        "request_id": request_id,
        "failure_mode": failure_mode,
        "disposition": "dismiss",
        "rebuttal": "x",
        "timestamp": "2026-04-22T12:00:00",
        "lesson_status": status,
        "validation_iterations": 1,
    }


class TestWriteAnalysisFreshWrite(unittest.TestCase):
    """No existing analysis — just writes analysis.yaml. No history,
    no feedback clearing (because there's nothing to clear)."""

    def test_writes_analysis_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            analysis = {
                "conversation_id": "c01",
                "date": "2026-04-22",
                "topic": "Test",
                "findings": [],
            }
            analyse.write_analysis(conv, analysis)

            self.assertTrue((conv / "analysis.yaml").exists())
            loaded = yaml.safe_load((conv / "analysis.yaml").read_text())
            self.assertEqual(loaded["conversation_id"], "c01")
            self.assertEqual(loaded["topic"], "Test")

    def test_does_not_create_history_file_on_fresh_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            analyse.write_analysis(conv, {"findings": []})
            self.assertFalse((conv / "analysis-history.yaml").exists())

    def test_leaves_unrelated_feedback_alone_on_fresh_write(self):
        """No prior analysis.yaml → no history entry → feedback.yaml not
        touched. (Contrived — shouldn't happen in practice — but pins
        'only delete feedback when snapshotting prior analysis'.)"""
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            (conv / FEEDBACK_FILENAME).write_text(
                yaml.dump([_feedback_item()]), encoding="utf-8",
            )
            analyse.write_analysis(conv, {"findings": []})
            self.assertTrue((conv / FEEDBACK_FILENAME).exists())


class TestWriteAnalysisSnapshotAndClear(unittest.TestCase):
    """The main supersede path: existing analysis → snapshotted into
    history with its feedback; feedback.yaml deleted."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conv = Path(self._tmp.name)
        self.old_analysis = {
            "conversation_id": "c01",
            "date": "2026-04-22",
            "git_ref": "old_ref",
            "topic": "Old topic",
            "findings": [{"request_id": "rq-c01-0001", "failure_mode": "FM-01"}],
        }
        (self.conv / "analysis.yaml").write_text(
            yaml.dump(self.old_analysis, sort_keys=False), encoding="utf-8",
        )
        (self.conv / FEEDBACK_FILENAME).write_text(
            yaml.dump([_feedback_item(status="validated")]), encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_new_analysis_becomes_visible_at_analysis_yaml(self):
        new = {"topic": "New topic", "findings": []}
        analyse.write_analysis(self.conv, new)

        loaded = yaml.safe_load((self.conv / "analysis.yaml").read_text())
        self.assertEqual(loaded["topic"], "New topic")

    def test_history_captures_old_analysis_and_its_feedback(self):
        analyse.write_analysis(self.conv, {"topic": "New", "findings": []})

        history = yaml.safe_load((self.conv / "analysis-history.yaml").read_text())
        self.assertEqual(len(history), 1)
        entry = history[0]
        self.assertEqual(entry["topic"], "Old topic")
        self.assertEqual(entry["findings"], self.old_analysis["findings"])
        self.assertEqual(len(entry["feedback"]), 1)
        self.assertEqual(entry["feedback"][0]["lesson_status"], "validated")
        self.assertIn("superseded_at", entry)

    def test_feedback_yaml_is_deleted_after_successful_write(self):
        analyse.write_analysis(self.conv, {"topic": "New", "findings": []})
        self.assertFalse((self.conv / FEEDBACK_FILENAME).exists())

    def test_preserves_existing_git_ref(self):
        """The git_ref reflects when the conversation happened, not when
        re-analysis ran — so an existing ref wins over the new analysis's."""
        new = {"topic": "New", "findings": [], "git_ref": "should_be_ignored"}
        analyse.write_analysis(self.conv, new)

        loaded = yaml.safe_load((self.conv / "analysis.yaml").read_text())
        self.assertEqual(loaded["git_ref"], "old_ref")


class TestWriteAnalysisHistoryRotation(unittest.TestCase):
    """Keep only the last N history entries to bound file growth."""

    def test_corrupt_history_file_starts_fresh(self):
        """Corrupt history YAML on disk must not block a new snapshot —
        snapshot starts a fresh list with just the prior analysis."""
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            (conv / "analysis.yaml").write_text(
                yaml.dump({"topic": "v1", "findings": []}), encoding="utf-8",
            )
            (conv / "analysis-history.yaml").write_text("not: valid: yaml: [[[")

            analyse.write_analysis(conv, {"topic": "v2", "findings": []})

            history = yaml.safe_load((conv / "analysis-history.yaml").read_text())
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["topic"], "v1")

    def test_rotates_when_exceeds_max_entries(self):
        """With MAX set low, old entries are dropped; newest N retained."""
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            existing = [
                {"topic": f"v{i}", "findings": [], "feedback": [],
                 "superseded_at": f"2026-04-2{i}T00:00:00+00:00"}
                for i in range(3)
            ]
            (conv / "analysis-history.yaml").write_text(
                yaml.dump(existing, sort_keys=False), encoding="utf-8",
            )
            # And an analysis.yaml to snapshot (becomes the 4th entry)
            (conv / "analysis.yaml").write_text(
                yaml.dump({"topic": "v3", "findings": []}), encoding="utf-8",
            )

            with patch.object(analyse, "ANALYSIS_HISTORY_MAX_ENTRIES", 2):
                analyse.write_analysis(conv, {"topic": "v4", "findings": []})

            history = yaml.safe_load((conv / "analysis-history.yaml").read_text())
            self.assertEqual(len(history), 2)
            # The two newest are retained — older v0, v1 dropped.
            topics = [h["topic"] for h in history]
            self.assertEqual(topics, ["v2", "v3"])


class TestWriteAnalysisFailureHandling(unittest.TestCase):
    """Failure modes around the commit point."""

    def test_failure_before_commit_leaves_disk_unchanged(self):
        """If the temp write fails, analysis.yaml and feedback.yaml must
        still reflect the prior state. The temp file should be cleaned up."""
        with tempfile.TemporaryDirectory() as tmp:
            conv = Path(tmp)
            old = {"topic": "preserved", "findings": [], "git_ref": "r1"}
            (conv / "analysis.yaml").write_text(yaml.dump(old), encoding="utf-8")
            (conv / FEEDBACK_FILENAME).write_text(
                yaml.dump([_feedback_item()]), encoding="utf-8",
            )

            # Simulate temp write failure by making write_text raise.
            from pathlib import Path as _PPath
            real_write = _PPath.write_text

            def boom(self, *args, **kwargs):
                if self.name.endswith(".tmp"):
                    raise OSError("disk full")
                return real_write(self, *args, **kwargs)

            with patch.object(_PPath, "write_text", boom):
                with self.assertRaises(OSError):
                    analyse.write_analysis(conv, {"topic": "new", "findings": []})

            loaded = yaml.safe_load((conv / "analysis.yaml").read_text())
            self.assertEqual(loaded["topic"], "preserved")
            self.assertTrue((conv / FEEDBACK_FILENAME).exists())
            self.assertFalse((conv / "analysis.yaml.tmp").exists())
            self.assertFalse((conv / "analysis-history.yaml.tmp").exists())


if __name__ == "__main__":
    unittest.main()
