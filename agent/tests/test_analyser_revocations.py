"""Tests for the passive analyser's finding-revocation post-processing.

The analyser asks the LLM to assign a unique short id to each finding
and to populate a ``revoked_findings`` list when it reconsiders any of
them. ``_apply_revocations`` filters those revoked entries out of
``findings`` before the analysis is persisted, with fuzzy-matching as
a safety net for single-character id typos.
"""

import unittest

from analyser.analyse import _apply_revocations, _is_within_edit_distance_1


def _finding(fid: str, *, request_id: str = "rq-c01-0001",
             failure_mode: str = "FM-01") -> dict:
    return {
        "id": fid,
        "request_id": request_id,
        "failure_mode": failure_mode,
        "failure_name": "Unnecessary tool call",
        "severity": "low",
        "detail": "...",
        "summary": "...",
    }


class TestApplyRevocations(unittest.TestCase):

    def test_exact_match_removes_finding(self):
        analysis = {
            "findings": [_finding("a3f9"), _finding("1c2e")],
            "revoked_findings": [{"id": "a3f9", "reason": "not actually a problem"}],
        }
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 1)
        self.assertEqual(len(analysis["findings"]), 1)
        self.assertEqual(analysis["findings"][0]["id"], "1c2e")

    def test_multiple_revocations_all_apply(self):
        analysis = {
            "findings": [_finding("a3f9"), _finding("1c2e"), _finding("9b4d")],
            "revoked_findings": [
                {"id": "a3f9", "reason": "ok actually"},
                {"id": "9b4d", "reason": "in zone status"},
            ],
        }
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 2)
        self.assertEqual([f["id"] for f in analysis["findings"]], ["1c2e"])

    def test_revoked_findings_preserved_on_analysis(self):
        """The revocation entries themselves stay on the analysis dict
        — metrics and the frontend need to read the reasons."""
        analysis = {
            "findings": [_finding("a3f9")],
            "revoked_findings": [{"id": "a3f9", "reason": "false positive"}],
        }
        _apply_revocations(analysis)
        self.assertEqual(len(analysis["revoked_findings"]), 1)
        self.assertEqual(analysis["revoked_findings"][0]["reason"], "false positive")

    def test_matched_revocations_enriched_with_original_finding(self):
        """Each matched revocation gets `original_finding` populated so
        metrics can read the failure_mode and frontend can render the
        original metadata without cross-referencing the filtered list."""
        original = _finding("a3f9", failure_mode="FM-11")
        analysis = {
            "findings": [original],
            "revoked_findings": [{"id": "a3f9", "reason": "in zone status"}],
        }
        _apply_revocations(analysis)
        self.assertIn("original_finding", analysis["revoked_findings"][0])
        self.assertEqual(
            analysis["revoked_findings"][0]["original_finding"]["failure_mode"],
            "FM-11",
        )

    def test_unmatched_revocations_not_enriched(self):
        """Revocations that don't match any finding shouldn't have a
        spurious `original_finding` attached."""
        analysis = {
            "findings": [_finding("a3f9")],
            "revoked_findings": [{"id": "zzzz", "reason": "no match"}],
        }
        _apply_revocations(analysis)
        self.assertNotIn("original_finding", analysis["revoked_findings"][0])

    def test_missing_revoked_findings_key_is_safe(self):
        analysis = {"findings": [_finding("a3f9")]}
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 0)
        self.assertEqual(len(analysis["findings"]), 1)

    def test_fuzzy_match_one_character_substitution(self):
        """Single character typo (a3f9 -> a3e9) still matches."""
        analysis = {
            "findings": [_finding("a3f9")],
            "revoked_findings": [{"id": "a3e9", "reason": "reconsidered"}],
        }
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 1)
        self.assertEqual(analysis["findings"], [])

    def test_ambiguous_fuzzy_match_is_skipped(self):
        """If a typo'd id is within distance 1 of two findings, refuse
        to guess — leave both findings in place."""
        analysis = {
            "findings": [_finding("a3f9"), _finding("a3e9")],
            "revoked_findings": [{"id": "a309", "reason": "?"}],
        }
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 0)
        self.assertEqual(len(analysis["findings"]), 2)

    def test_malformed_revocation_entries_are_ignored(self):
        analysis = {
            "findings": [_finding("a3f9")],
            "revoked_findings": [
                "not a dict",
                {"reason": "missing id"},
                {"id": "", "reason": "empty id"},
                {"id": None, "reason": "null id"},
                {"id": "a3f9", "reason": "valid"},
            ],
        }
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 1)
        self.assertEqual(analysis["findings"], [])

    def test_findings_without_ids_are_unmatchable(self):
        """A finding emitted without an id can't be revoked — by design."""
        analysis = {
            "findings": [
                {"request_id": "rq-c01-0001", "failure_mode": "FM-01"},  # no id
            ],
            "revoked_findings": [{"id": "a3f9", "reason": "?"}],
        }
        removed = _apply_revocations(analysis)
        self.assertEqual(removed, 0)
        self.assertEqual(len(analysis["findings"]), 1)


class TestIsWithinEditDistance1(unittest.TestCase):

    def test_equal_strings(self):
        self.assertTrue(_is_within_edit_distance_1("a3f9", "a3f9"))

    def test_single_substitution(self):
        self.assertTrue(_is_within_edit_distance_1("a3f9", "a3e9"))

    def test_single_deletion(self):
        self.assertTrue(_is_within_edit_distance_1("a3f9", "a3f"))

    def test_single_insertion(self):
        self.assertTrue(_is_within_edit_distance_1("a3f9", "a3f9b"))

    def test_two_substitutions_rejected(self):
        self.assertFalse(_is_within_edit_distance_1("a3f9", "b3e9"))

    def test_length_difference_2_rejected(self):
        self.assertFalse(_is_within_edit_distance_1("a3f9", "a3"))

    def test_empty_string(self):
        self.assertTrue(_is_within_edit_distance_1("a", ""))
        self.assertFalse(_is_within_edit_distance_1("ab", ""))


if __name__ == "__main__":
    unittest.main()
