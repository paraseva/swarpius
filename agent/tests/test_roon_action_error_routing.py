"""Classifier tests for _classify_item_error in roon_action.py.

Pins which bucket each shape of per-item ValueError lands in after
_execute_library_action_for_item raises. Prior to the fix the
unknown-reference bucket was keyed on "unknown media reference" — a
substring that no code path produced — so the structured_errors array
carried "not found" failures in the generic `other_errors` bag rather
than the clean `{refs:[...], error:"Unknown reference(s)"}` shape.
"""

import unittest

from tools.roon_action import _classify_item_error


class TestClassifyItemError(unittest.TestCase):

    def test_reference_not_found_routes_to_unknown_ref(self) -> None:
        """The `has_reference` guard raises this when an action names a
        ref that isn't in search history. Before the fix this fell
        through to `other_errors`.
        """
        msg = (
            "Reference 'S:abc12' not found. "
            "Check that it matches a reference from the search results exactly."
        )
        self.assertEqual(_classify_item_error(msg), "unknown_ref")

    def test_title_match_failure_routes_to_no_title_match(self) -> None:
        """NoTitleMatch recovery path: the ref is unknown *and* title
        rescue couldn't save it. Verbose message kept intact.
        """
        msg = (
            "Unknown reference 'S:xyz89' and no title match for 'Song' "
            "in search history. Check that the reference and title both "
            "come from recent search results."
        )
        self.assertEqual(_classify_item_error(msg), "no_title_match")

    def test_ambiguous_title_routes_to_ambiguous_bucket(self) -> None:
        msg = (
            "Ambiguous title, reference tied: 'Song' matches 2 items "
            "in search history (S:abc12, S:xyz89) with equal distance "
            "to submitted reference S:def34. Re-search or specify which one."
        )
        self.assertEqual(_classify_item_error(msg), "ambiguous_title")

    def test_unrelated_error_routes_to_other(self) -> None:
        self.assertEqual(
            _classify_item_error("Transport failure: timeout after 30s"),
            "other",
        )

    def test_classifier_is_case_insensitive(self) -> None:
        """The implementation lowers before matching; all callers pass
        the raw ValueError.str() which may mix cases.
        """
        self.assertEqual(
            _classify_item_error("REFERENCE 'S:x' NOT FOUND. check..."),
            "unknown_ref",
        )


if __name__ == "__main__":
    unittest.main()
