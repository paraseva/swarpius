"""TDD red: tests for the identity-based add_feedback_item contract.

The passive-analyser's add_feedback_item (a CLI helper not on the
user-facing flow) should use the same (request_id, failure_mode)
identity model as the websocket-facing submit_feedback, for symmetry.

Contract:

- add_feedback_item(conv_dir, request_id, failure_mode, disposition,
  rebuttal) — no more finding_index.
- Re-adding on the same identity replaces the existing entry
  (fresh pending, zeroed validation_iterations).
- Different identities append.
- Persisted entries carry request_id + failure_mode, not finding_index.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyser.feedback import add_feedback_item, read_feedback  # noqa: E402


class TestAddFeedbackItemIdentity(unittest.TestCase):

    def test_persists_with_identity_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = add_feedback_item(
                Path(tmp),
                request_id="rq-c01-0001", failure_mode="FM-08",
                disposition="dismiss", rebuttal="x",
            )
            self.assertEqual(item["request_id"], "rq-c01-0001")
            self.assertEqual(item["failure_mode"], "FM-08")
            self.assertNotIn("finding_index", item)
            self.assertEqual(item["lesson_status"], "pending")
            self.assertEqual(item["validation_iterations"], 0)

    def test_different_identities_both_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            add_feedback_item(
                Path(tmp), request_id="rq-c01-0001",
                failure_mode="FM-08", disposition="dismiss", rebuttal="A",
            )
            add_feedback_item(
                Path(tmp), request_id="rq-c01-0002",
                failure_mode="FM-12", disposition="downgrade", rebuttal="B",
            )
            items = read_feedback(Path(tmp))
        self.assertEqual(len(items), 2)
        identities = {(i["request_id"], i["failure_mode"]) for i in items}
        self.assertEqual(identities, {
            ("rq-c01-0001", "FM-08"),
            ("rq-c01-0002", "FM-12"),
        })

    def test_same_identity_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            add_feedback_item(
                Path(tmp), request_id="rq-c01-0001",
                failure_mode="FM-08", disposition="dismiss", rebuttal="first",
            )
            add_feedback_item(
                Path(tmp), request_id="rq-c01-0001",
                failure_mode="FM-08", disposition="downgrade", rebuttal="second",
            )
            items = read_feedback(Path(tmp))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["disposition"], "downgrade")
        self.assertEqual(items[0]["rebuttal"], "second")
        self.assertEqual(items[0]["lesson_status"], "pending")
        self.assertEqual(items[0]["validation_iterations"], 0)

if __name__ == "__main__":
    unittest.main()
