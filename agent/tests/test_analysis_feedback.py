"""Tests for passive-analyser feedback: storage, lessons management, validation checks.

add_feedback_item's identity contract is covered separately in
test_analysis_feedback_identity.py.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyser.feedback import (
    FEEDBACK_FILENAME,
    build_analyser_prompt,
    check_finding_resolved,
    read_feedback,
    read_lessons,
    write_feedback,
    write_lesson,
)

# ---------------------------------------------------------------------------
# Feedback storage
# ---------------------------------------------------------------------------


class TestReadFeedback(unittest.TestCase):
    """read_feedback() — reads feedback.yaml from a conversation directory."""

    def test_returns_empty_list_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = read_feedback(Path(tmp))
        self.assertEqual(result, [])

    def test_returns_empty_list_for_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / FEEDBACK_FILENAME).write_text("", encoding="utf-8")
            result = read_feedback(Path(tmp))
        self.assertEqual(result, [])

    def test_reads_back_written_items(self):
        items = [
            {
                "request_id": "rq-c01-0001",
                "failure_mode": "FM-08",
                "disposition": "dismiss",
                "rebuttal": "This was correct behaviour.",
                "timestamp": "2026-04-02T21:00:00",
                "lesson_status": "pending",
                "validation_iterations": 0,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_feedback(Path(tmp), items)
            result = read_feedback(Path(tmp))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["disposition"], "dismiss")
        self.assertEqual(result[0]["rebuttal"], "This was correct behaviour.")


# ---------------------------------------------------------------------------
# Lessons management
# ---------------------------------------------------------------------------


class TestReadLessons(unittest.TestCase):
    """read_lessons() — reads lessons-learned.md content."""

    def test_returns_empty_string_when_no_file(self):
        result = read_lessons(Path("/nonexistent/path/lessons-learned.md"))
        self.assertEqual(result, "")

    def test_reads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            path.write_text("# Lessons\n\nSome lesson.", encoding="utf-8")
            result = read_lessons(path)
        self.assertIn("Some lesson.", result)


class TestWriteLesson(unittest.TestCase):
    """write_lesson() — adds or updates a lesson in lessons-learned.md."""

    def test_creates_file_with_header_and_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            write_lesson(
                path,
                heading="Browse session lifecycle",
                body="References are stable identifiers.",
                source="c01-0001 (2026-04-02), FM-08 dismissed",
            )
            content = path.read_text(encoding="utf-8")
        self.assertIn("# Lessons Learned", content)
        self.assertIn("## Browse session lifecycle", content)
        self.assertIn("References are stable identifiers.", content)
        self.assertIn("c01-0001 (2026-04-02), FM-08 dismissed", content)

    def test_appends_second_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            write_lesson(path, "First topic", "First body.", "src-1")
            write_lesson(path, "Second topic", "Second body.", "src-2")
            content = path.read_text(encoding="utf-8")
        self.assertIn("## First topic", content)
        self.assertIn("## Second topic", content)
        # Both bodies present
        self.assertIn("First body.", content)
        self.assertIn("Second body.", content)

    def test_updates_existing_lesson_by_heading_and_source(self):
        """Same (heading, source) → update in place. Different source
        with the same heading is a distinct lesson (tested separately)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            write_lesson(path, "Browse sessions", "Old content.", "src-1")
            write_lesson(path, "Browse sessions", "Updated content.", "src-1")
            content = path.read_text(encoding="utf-8")
        self.assertIn("Updated content.", content)
        self.assertNotIn("Old content.", content)
        # Heading should appear exactly once
        self.assertEqual(content.count("## Browse sessions"), 1)

    def test_different_source_with_same_heading_are_both_kept(self):
        """Dedup key is (heading, source). Two feedback items that
        produce the same heading from different sources each get their
        own entry so neither loses its attribution."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            write_lesson(path, "Browse sessions", "From request 1.", "src-1")
            write_lesson(path, "Browse sessions", "From request 2.", "src-2")
            content = path.read_text(encoding="utf-8")
        # Both bodies and both source attributions survive
        self.assertIn("From request 1.", content)
        self.assertIn("From request 2.", content)
        self.assertIn("src-1", content)
        self.assertIn("src-2", content)

    def test_preserves_other_lessons_on_update(self):
        """Updating one lesson (by matching heading+source) leaves other
        lessons untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            write_lesson(path, "Topic A", "Body A.", "src-a")
            write_lesson(path, "Topic B", "Body B.", "src-b")
            write_lesson(path, "Topic A", "New A.", "src-a")  # updates in place
            content = path.read_text(encoding="utf-8")
        self.assertIn("New A.", content)
        self.assertIn("Body B.", content)
        self.assertNotIn("Body A.", content)


class TestBuildAnalyserPrompt(unittest.TestCase):
    """build_analyser_prompt() — combines guide text with lessons."""

    def test_guide_only_when_no_lessons(self):
        guide = "# Analysis Guide\n\nSome rules."
        result = build_analyser_prompt(guide, Path("/nonexistent/lessons.md"))
        self.assertEqual(result, guide)

    def test_appends_lessons_after_guide(self):
        guide = "# Analysis Guide\n\nSome rules."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons-learned.md"
            path.write_text("# Lessons Learned\n\n## Topic\nContent.", encoding="utf-8")
            result = build_analyser_prompt(guide, path)
        self.assertIn("# Analysis Guide", result)
        self.assertIn("# Lessons Learned", result)
        # Guide comes first
        guide_pos = result.index("# Analysis Guide")
        lessons_pos = result.index("# Lessons Learned")
        self.assertLess(guide_pos, lessons_pos)


# ---------------------------------------------------------------------------
# Validation check
# ---------------------------------------------------------------------------


SAMPLE_FINDING = {
    "request_id": "rq-c01-0001",
    "failure_mode": "FM-08",
    "failure_name": "Failed context reference",
    "severity": "medium",
    "summary": "Used stale reference from cached playlist.",
}


class TestCheckFindingResolved(unittest.TestCase):
    """check_finding_resolved() — deterministic comparison of original vs re-analysis."""

    def test_dismiss_finding_gone_is_validated(self):
        """Finding absent from re-analysis → validated."""
        new_analysis = {"findings": []}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "dismiss")
        self.assertEqual(result, "validated")

    def test_dismiss_finding_still_present_is_not_resolved(self):
        """Same finding still in re-analysis → not_resolved."""
        new_analysis = {"findings": [
            {"request_id": "rq-c01-0001", "failure_mode": "FM-08", "severity": "medium"},
        ]}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "dismiss")
        self.assertEqual(result, "not_resolved")

    def test_dismiss_different_finding_unaffected(self):
        """A different finding (different FM or request_id) doesn't count."""
        new_analysis = {"findings": [
            {"request_id": "rq-c01-0001", "failure_mode": "FM-12", "severity": "low"},
        ]}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "dismiss")
        self.assertEqual(result, "validated")

    def test_downgrade_severity_lowered_is_validated(self):
        """Severity reduced from medium to low → validated."""
        new_analysis = {"findings": [
            {"request_id": "rq-c01-0001", "failure_mode": "FM-08", "severity": "low"},
        ]}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "downgrade")
        self.assertEqual(result, "validated")

    def test_downgrade_severity_unchanged_is_not_resolved(self):
        """Same severity → not_resolved."""
        new_analysis = {"findings": [
            {"request_id": "rq-c01-0001", "failure_mode": "FM-08", "severity": "medium"},
        ]}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "downgrade")
        self.assertEqual(result, "not_resolved")

    def test_downgrade_finding_gone_is_validated(self):
        """Finding completely gone on downgrade → also counts as validated (better than expected)."""
        new_analysis = {"findings": []}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "downgrade")
        self.assertEqual(result, "validated")

    def test_empty_findings_list(self):
        """New analysis with no findings key defaults to empty."""
        new_analysis = {}
        result = check_finding_resolved(SAMPLE_FINDING, new_analysis, "dismiss")
        self.assertEqual(result, "validated")


if __name__ == "__main__":
    unittest.main()
