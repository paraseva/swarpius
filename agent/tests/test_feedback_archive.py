"""Tests for feedback archival during log cleanup.

When cleanup_old_logs deletes expired date directories, any feedback.yaml
and analysis.yaml files should be copied to data/analysis/feedback/ first
so that operator feedback and analysis context survive log rotation.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import yaml


class TestFeedbackArchiveDir(unittest.TestCase):
    """feedback_archive_dir() returns the correct path under analysis_dir."""

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"})
    def test_returns_feedback_subdir_of_analysis(self):
        from app.data_paths import feedback_archive_dir

        self.assertEqual(
            feedback_archive_dir(), Path("/tmp/test-swarpius/analysis/feedback")
        )

    def test_ensure_dirs_creates_feedback_archive(self):
        from app.data_paths import ensure_dirs, feedback_archive_dir

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                ensure_dirs()
                self.assertTrue(feedback_archive_dir().is_dir())


class TestCleanupArchivesFeedback(unittest.TestCase):
    """cleanup_old_logs archives feedback before deleting expired dirs."""

    def _make_date_dir(self, root: Path, date_str: str, conv_id: str,
                       feedback: list | None = None,
                       analysis: dict | None = None) -> Path:
        """Create a date/conversation directory with optional feedback and analysis."""
        conv_dir = root / date_str / conv_id
        conv_dir.mkdir(parents=True)
        if feedback is not None:
            (conv_dir / "feedback.yaml").write_text(
                yaml.dump(feedback, default_flow_style=False),
                encoding="utf-8",
            )
        if analysis is not None:
            (conv_dir / "analysis.yaml").write_text(
                yaml.dump(analysis, default_flow_style=False),
                encoding="utf-8",
            )
        return conv_dir

    def _expired_date(self, days_ago: int = 10) -> str:
        return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    def _recent_date(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def test_feedback_archived_before_deletion(self):
        from app.runtime.request_logger import cleanup_old_logs

        with tempfile.TemporaryDirectory() as tmp:
            logs_root = Path(tmp) / "logs"
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()

            date_str = self._expired_date()
            feedback = [{"request_id": "rq-c01-0001", "failure_mode": "FM-08", "disposition": "dismiss",
                         "rebuttal": "False positive", "lesson_status": "validated"}]
            self._make_date_dir(logs_root, date_str, "c01", feedback=feedback)

            with patch("app.runtime.request_logger.feedback_archive_dir",
                       return_value=archive_root):
                removed = cleanup_old_logs(logs_root, retention_days=7)

            self.assertEqual(removed, 1)
            # Original dir is gone
            self.assertFalse((logs_root / date_str).exists())
            # Feedback is archived
            archived = archive_root / date_str / "c01" / "feedback.yaml"
            self.assertTrue(archived.exists())
            data = yaml.safe_load(archived.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["disposition"], "dismiss")

    def test_analysis_archived_alongside_feedback(self):
        from app.runtime.request_logger import cleanup_old_logs

        with tempfile.TemporaryDirectory() as tmp:
            logs_root = Path(tmp) / "logs"
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()

            date_str = self._expired_date()
            feedback = [{"request_id": "rq-c01-0001", "failure_mode": "FM-08", "disposition": "downgrade",
                         "rebuttal": "Agreed"}]
            analysis = {"findings": [{"failure_mode": "FM-01"}]}
            self._make_date_dir(logs_root, date_str, "c02",
                                feedback=feedback, analysis=analysis)

            with patch("app.runtime.request_logger.feedback_archive_dir",
                       return_value=archive_root):
                cleanup_old_logs(logs_root, retention_days=7)

            archived_analysis = archive_root / date_str / "c02" / "analysis.yaml"
            self.assertTrue(archived_analysis.exists())
            data = yaml.safe_load(archived_analysis.read_text(encoding="utf-8"))
            self.assertEqual(data["findings"][0]["failure_mode"], "FM-01")

    def test_no_feedback_means_no_archive(self):
        from app.runtime.request_logger import cleanup_old_logs

        with tempfile.TemporaryDirectory() as tmp:
            logs_root = Path(tmp) / "logs"
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()

            date_str = self._expired_date()
            self._make_date_dir(logs_root, date_str, "c01")

            with patch("app.runtime.request_logger.feedback_archive_dir",
                       return_value=archive_root):
                removed = cleanup_old_logs(logs_root, retention_days=7)

            self.assertEqual(removed, 1)
            # No archive created for this date
            self.assertFalse((archive_root / date_str).exists())

    def test_recent_dirs_untouched(self):
        from app.runtime.request_logger import cleanup_old_logs

        with tempfile.TemporaryDirectory() as tmp:
            logs_root = Path(tmp) / "logs"
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()

            date_str = self._recent_date()
            feedback = [{"request_id": "rq-c01-0001", "failure_mode": "FM-08", "disposition": "dismiss",
                         "rebuttal": "Not relevant"}]
            self._make_date_dir(logs_root, date_str, "c01", feedback=feedback)

            with patch("app.runtime.request_logger.feedback_archive_dir",
                       return_value=archive_root):
                removed = cleanup_old_logs(logs_root, retention_days=7)

            self.assertEqual(removed, 0)
            # Original still there
            self.assertTrue((logs_root / date_str / "c01" / "feedback.yaml").exists())
            # Nothing archived
            self.assertFalse((archive_root / date_str).exists())

    def test_multiple_conversations_archived(self):
        from app.runtime.request_logger import cleanup_old_logs

        with tempfile.TemporaryDirectory() as tmp:
            logs_root = Path(tmp) / "logs"
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()

            date_str = self._expired_date()
            self._make_date_dir(logs_root, date_str, "c01",
                                feedback=[{"request_id": "rq-c01-0001", "failure_mode": "FM-08", "disposition": "dismiss",
                                           "rebuttal": "FP"}])
            self._make_date_dir(logs_root, date_str, "c02",
                                feedback=[{"request_id": "rq-c01-0002", "failure_mode": "FM-12", "disposition": "downgrade",
                                           "rebuttal": "Real issue"}])
            # c03 has no feedback
            self._make_date_dir(logs_root, date_str, "c03")

            with patch("app.runtime.request_logger.feedback_archive_dir",
                       return_value=archive_root):
                cleanup_old_logs(logs_root, retention_days=7)

            self.assertTrue((archive_root / date_str / "c01" / "feedback.yaml").exists())
            self.assertTrue((archive_root / date_str / "c02" / "feedback.yaml").exists())
            self.assertFalse((archive_root / date_str / "c03").exists())

    def test_existing_archive_not_overwritten(self):
        """If feedback was already archived (e.g. re-run), don't overwrite it."""
        from app.runtime.request_logger import cleanup_old_logs

        with tempfile.TemporaryDirectory() as tmp:
            logs_root = Path(tmp) / "logs"
            archive_root = Path(tmp) / "archive"

            date_str = self._expired_date()
            self._make_date_dir(logs_root, date_str, "c01",
                                feedback=[{"request_id": "rq-c01-0001", "failure_mode": "FM-08", "disposition": "dismiss",
                                           "rebuttal": "New"}])

            # Pre-existing archive
            existing_dir = archive_root / date_str / "c01"
            existing_dir.mkdir(parents=True)
            (existing_dir / "feedback.yaml").write_text(
                yaml.dump([{"request_id": "rq-c01-0001", "failure_mode": "FM-08", "disposition": "dismiss",
                            "rebuttal": "Original"}]),
                encoding="utf-8",
            )

            with patch("app.runtime.request_logger.feedback_archive_dir",
                       return_value=archive_root):
                cleanup_old_logs(logs_root, retention_days=7)

            # Original archive preserved
            data = yaml.safe_load(
                (existing_dir / "feedback.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(data[0]["rebuttal"], "Original")
