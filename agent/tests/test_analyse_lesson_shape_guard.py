"""Regression tests for the lesson-shape guard in analyse.py.

Prior to the guard, `_extract_lesson` returned whatever
`parse_json_response` produced — which could be a list (if the LLM
echoed the consolidate/batch schema), a dict missing
heading/body/source, or a dict with non-string values. The caller
then did unguarded `lesson_json["heading"]` indexing in
`process_feedback`, raising TypeError/KeyError. Because
`process_all_pending_feedback` has no per-item except handler, one
malformed LLM response aborted the entire feedback pass and left
the item jammed in `lesson_status: processing` until the 10-minute
stuck-recovery sweep.

The guard validates shape at the boundary so the caller can keep
doing plain indexing.
"""

import unittest

from analyser.analyse import _valid_lesson_shape  # noqa: E402


class TestValidLessonShape(unittest.TestCase):

    def test_accepts_well_formed_dict(self) -> None:
        obj = {
            "heading": "Don't loop on empty searches",
            "body": "If the first query returns nothing, ...",
            "source": "feedback c07",
        }
        self.assertTrue(_valid_lesson_shape(obj))

    def test_accepts_dict_with_extra_keys(self) -> None:
        """Extra keys from the LLM are fine — we only require the
        three we index into.
        """
        obj = {
            "heading": "h",
            "body": "b",
            "source": "s",
            "reasoning": "the model reasoned about it",
        }
        self.assertTrue(_valid_lesson_shape(obj))

    def test_rejects_none(self) -> None:
        self.assertFalse(_valid_lesson_shape(None))

    def test_rejects_list(self) -> None:
        """parse_json_response can return a list when the LLM produces
        an array — e.g. echoing the consolidate-lessons schema.
        """
        self.assertFalse(_valid_lesson_shape([{"heading": "x"}]))

    def test_rejects_string(self) -> None:
        self.assertFalse(_valid_lesson_shape("heading: x"))

    def test_rejects_missing_heading(self) -> None:
        self.assertFalse(_valid_lesson_shape({"body": "b", "source": "s"}))

    def test_rejects_non_string_value(self) -> None:
        """An LLM might emit a nested dict or a number for a field.
        Reject — the caller does string formatting.
        """
        self.assertFalse(_valid_lesson_shape({
            "heading": 42,
            "body": "b",
            "source": "s",
        }))

    def test_rejects_whitespace_only_value(self) -> None:
        self.assertFalse(_valid_lesson_shape({
            "heading": "h",
            "body": "   \n ",
            "source": "s",
        }))


if __name__ == "__main__":
    unittest.main()
