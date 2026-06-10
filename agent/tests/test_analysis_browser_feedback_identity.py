"""TDD red: tests for the identity-based feedback contract on the backend.

Replaces the positional finding_index model with stable
(request_id, failure_mode) identity. These tests will fail against
current code — they drive the Stage 2 implementation.

Contract being pinned:

- submit_feedback(logs_root, date, conv_id, request_id, failure_mode,
  disposition, rebuttal) — no more finding_index.
- submit_feedback validates that the (request_id, failure_mode)
  identity matches a finding in the current analysis.yaml and rejects
  submissions for identities that aren't present.
- Re-submitting on the same (request_id, failure_mode) replaces the
  existing entry (fresh pending, zeroed validation_iterations,
  updated timestamp). Entries for *other* identities are preserved.
- Persisted feedback entries carry request_id + failure_mode, no
  longer carry finding_index.
- get_feedback_status returns entries with the new schema.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from app.analysis.feedback import (
    FEEDBACK_FILENAME,
    get_feedback_status,
    submit_feedback,
)

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
    return yaml.safe_load((conv_dir / FEEDBACK_FILENAME).read_text(encoding="utf-8"))


class TestSubmitFeedbackIdentity(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)
        self.date = "2026-04-22"
        self.conv_id = "c01"
        self.conv_dir = self.logs_root / self.date / self.conv_id
        _write_analysis(self.conv_dir, SAMPLE_FINDINGS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_valid_identity_persists_with_identity_fields(self):
        """Happy path: entry stored with request_id + failure_mode; no finding_index."""
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="false positive",
        )
        self.assertTrue(result["ok"])

        item = _load_feedback(self.conv_dir)[0]
        self.assertEqual(item["request_id"], "rq-c01-0001")
        self.assertEqual(item["failure_mode"], "FM-08")
        self.assertNotIn("finding_index", item)
        self.assertEqual(item["disposition"], "dismiss")
        self.assertEqual(item["lesson_status"], "pending")
        self.assertEqual(item["validation_iterations"], 0)

    def test_identity_not_in_current_analysis_rejected(self):
        """Submitting against an identity that doesn't match any finding
        in the current analysis is rejected — this prevents stale UI
        submissions from producing orphans on disk."""
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0099", failure_mode="FM-99",
            disposition="dismiss", rebuttal="x",
        )
        self.assertFalse(result["ok"])
        self.assertIn("rq-c01-0099", result["error"])
        self.assertFalse((self.conv_dir / FEEDBACK_FILENAME).exists())

    def test_missing_analysis_yaml_rejected(self):
        empty_conv = self.logs_root / self.date / "c02"
        empty_conv.mkdir(parents=True)
        result = submit_feedback(
            self.logs_root, self.date, "c02",
            request_id="rq-c02-0001", failure_mode="FM-01",
            disposition="dismiss", rebuttal="x",
        )
        self.assertFalse(result["ok"])
        self.assertIn("analysis.yaml", result["error"])

    def test_missing_conversation_dir_rejected(self):
        result = submit_feedback(
            self.logs_root, self.date, "c99",
            request_id="rq-c99-0001", failure_mode="FM-01",
            disposition="dismiss", rebuttal="x",
        )
        self.assertFalse(result["ok"])

    def test_second_submission_on_same_identity_replaces(self):
        """Operator changes disposition on the same finding → replace,
        not accumulate. The entry's lesson_status resets to pending and
        validation_iterations to 0 so the analyser treats it as fresh."""
        submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="first take",
        )
        submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="downgrade", rebuttal="actually severity was wrong",
        )

        items = _load_feedback(self.conv_dir)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["disposition"], "downgrade")
        self.assertEqual(items[0]["rebuttal"], "actually severity was wrong")
        self.assertEqual(items[0]["lesson_status"], "pending")
        self.assertEqual(items[0]["validation_iterations"], 0)

    def test_invalid_disposition_rejected(self):
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="bogus", rebuttal="x",
        )
        self.assertFalse(result["ok"])
        self.assertIn("bogus", result["error"])

    def test_whitespace_only_rebuttal_rejected(self):
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="   \n\t",
        )
        self.assertFalse(result["ok"])

    def test_rebuttal_whitespace_is_stripped_on_persist(self):
        submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="  real feedback  \n",
        )
        item = _load_feedback(self.conv_dir)[0]
        self.assertEqual(item["rebuttal"], "real feedback")

    def test_corrupt_existing_feedback_is_replaced_not_appended(self):
        """If feedback.yaml exists but isn't a list, start fresh rather
        than crashing."""
        (self.conv_dir / FEEDBACK_FILENAME).write_text(
            "not_a_list: true\n", encoding="utf-8",
        )
        result = submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="x",
        )
        self.assertTrue(result["ok"])
        items = _load_feedback(self.conv_dir)
        self.assertEqual(len(items), 1)


class TestGetFeedbackStatusIdentity(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)
        self.date = "2026-04-22"
        self.conv_id = "c01"
        self.conv_dir = self.logs_root / self.date / self.conv_id
        _write_analysis(self.conv_dir, SAMPLE_FINDINGS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_items_with_identity_schema(self):
        submit_feedback(
            self.logs_root, self.date, self.conv_id,
            request_id="rq-c01-0001", failure_mode="FM-08",
            disposition="dismiss", rebuttal="x",
        )
        result = get_feedback_status(self.logs_root, self.date, self.conv_id)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["request_id"], "rq-c01-0001")
        self.assertEqual(item["failure_mode"], "FM-08")
        self.assertNotIn("finding_index", item)

    def test_no_feedback_file_returns_empty(self):
        result = get_feedback_status(self.logs_root, self.date, self.conv_id)
        self.assertEqual(result, {"ok": True, "items": []})

    def test_missing_conversation_dir_returns_empty(self):
        """Tolerant read — nonexistent conv indistinguishable from
        'no feedback yet'."""
        result = get_feedback_status(self.logs_root, self.date, "c99")
        self.assertEqual(result, {"ok": True, "items": []})

    def test_malformed_yaml_returns_empty(self):
        """Corrupt feedback.yaml (non-list) → empty list, matching the
        submit-side 'start fresh' behaviour."""
        (self.conv_dir / FEEDBACK_FILENAME).write_text(
            "not_a_list: true\n", encoding="utf-8",
        )
        result = get_feedback_status(self.logs_root, self.date, self.conv_id)
        self.assertEqual(result, {"ok": True, "items": []})


if __name__ == "__main__":
    unittest.main()
