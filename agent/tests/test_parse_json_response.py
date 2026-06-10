"""Tests for ``llm_layer.parse_json_response``.

The function tries three strategies in order: direct ``json.loads``,
extraction from a fenced code block, and brace-scan with depth counting.
Silent failure here drops a finding from the analyser, so each fallback
path is pinned independently.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ANALYSER_DIR = Path(__file__).resolve().parents[2] / "passive-analyser"
if str(ANALYSER_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSER_DIR))

from analyser.llm_layer import parse_json_response  # noqa: E402


class TestParseJsonResponse(unittest.TestCase):
    def test_direct_object_parses(self):
        self.assertEqual(
            parse_json_response('{"a": 1, "b": "x"}'),
            {"a": 1, "b": "x"},
        )

    def test_direct_array_parses(self):
        self.assertEqual(
            parse_json_response('[{"a": 1}, {"a": 2}]'),
            [{"a": 1}, {"a": 2}],
        )

    def test_strips_surrounding_whitespace_before_direct_parse(self):
        self.assertEqual(parse_json_response('  \n  {"a": 1}  \n'), {"a": 1})

    def test_extracts_from_fenced_json_block(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nHope that helps.'
        self.assertEqual(parse_json_response(text), {"a": 1})

    def test_extracts_from_unlabelled_fenced_block(self):
        text = 'prefix\n```\n[1, 2, 3]\n```\nsuffix'
        self.assertEqual(parse_json_response(text), [1, 2, 3])

    def test_brace_scan_recovers_simple_object_with_prose_around(self):
        text = 'Sure! The answer is {"a": 1, "b": 2}. Done.'
        self.assertEqual(parse_json_response(text), {"a": 1, "b": 2})

    def test_brace_scan_recovers_array_with_prose_around(self):
        text = 'Result: [{"a": 1}, {"b": 2}] — two findings.'
        self.assertEqual(parse_json_response(text), [{"a": 1}, {"b": 2}])

    def test_brace_scan_handles_nested_object_depth_correctly(self):
        # The brace-scan must not stop at the first '}' — nested objects
        # need depth tracking. (No nested arrays here — see the
        # nested-array note below.)
        text = 'noise {"outer": {"inner": {"deep": 42}}} more noise'
        self.assertEqual(
            parse_json_response(text),
            {"outer": {"inner": {"deep": 42}}},
        )

    def test_brace_scan_prefers_array_over_enclosing_object(self):
        # Quirk: the fallback iterates ('[', ']') before ('{', '}'), so
        # when prose surrounds an object that *contains* an array, the
        # inner array is returned and the outer object is missed.
        # Pinned to flag the behaviour — well-formed analyser output
        # parses via the direct path so this only bites broken output.
        text = 'Sure! The answer is {"a": {"b": 1}, "c": [1,2]}. Done.'
        self.assertEqual(parse_json_response(text), [1, 2])

    def test_returns_none_for_empty_input(self):
        self.assertIsNone(parse_json_response(""))

    def test_returns_none_for_pure_prose(self):
        self.assertIsNone(parse_json_response("This is just text, no JSON here."))

    def test_returns_none_for_unbalanced_braces(self):
        # Brace-scan should hit end of string without depth returning to 0.
        self.assertIsNone(parse_json_response('garbage {"a": 1, "b": '))

    def test_returns_none_for_malformed_json_in_fenced_block(self):
        # Fenced block matched but contents won't parse, and no other
        # balanced object/array exists in the text.
        text = '```json\n{"a": not valid}\n```'
        self.assertIsNone(parse_json_response(text))


if __name__ == "__main__":
    unittest.main()
