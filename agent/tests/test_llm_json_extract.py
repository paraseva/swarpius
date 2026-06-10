"""Tests for ``extract_json_object`` — the shared lenient JSON
extractor used by the arbiter and diagnostic agent.

Documents the contract: tolerate markdown fences, preamble reasoning,
and other surrounding text; take the last JSON object as the model's
final answer; raise on no-JSON / malformed inputs so callers can
fall back deterministically.
"""

from __future__ import annotations

import json
import unittest

from app.llm.json_extract import extract_json_object


class TestExtractJsonObject(unittest.TestCase):
    def test_raw_json_round_trips(self):
        self.assertEqual(extract_json_object('{"x": 1}'), {"x": 1})

    def test_fenced_json_with_language_tag(self):
        text = '```json\n{"action": "queue"}\n```'
        self.assertEqual(extract_json_object(text), {"action": "queue"})

    def test_fenced_json_without_language_tag(self):
        text = '```\n{"action": "queue"}\n```'
        self.assertEqual(extract_json_object(text), {"action": "queue"})

    def test_json_with_preamble_text(self):
        text = 'Let me think briefly. {"action": "queue"}'
        self.assertEqual(extract_json_object(text), {"action": "queue"})

    def test_multiple_objects_returns_last(self):
        """Models often reason with a draft object before committing to
        a final one. The convention (matching diagnostic_agent's
        historical behaviour) is that the last object wins."""
        text = '{"draft": 1} ... {"final": 2}'
        self.assertEqual(extract_json_object(text), {"final": 2})

    def test_multiline_json_inside_fences(self):
        text = (
            '```json\n'
            '{\n'
            '  "action": "interrupt_and_replace",\n'
            '  "confidence": 0.95\n'
            '}\n'
            '```'
        )
        self.assertEqual(
            extract_json_object(text),
            {"action": "interrupt_and_replace", "confidence": 0.95},
        )

    def test_haiku_observed_failure_pattern(self):
        """Exact response shape that broke the arbiter on 2026-05-28
        (Haiku 4.5 wrapping decision JSON in markdown fences with a
        language tag and embedded apostrophes in the reason string)."""
        text = (
            '```json\n'
            '{\n'
            '  "action": "interrupt_and_replace",\n'
            '  "reason": "User explicitly cancels the previous multi-track '
            "playlist request with 'forget that' and requests a single "
            'specific track instead.",\n'
            '  "confidence": 0.95\n'
            '}\n'
            '```'
        )
        result = extract_json_object(text)
        self.assertEqual(result["action"], "interrupt_and_replace")
        self.assertEqual(result["confidence"], 0.95)
        self.assertIn("forget that", result["reason"])

    def test_empty_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            extract_json_object("")

    def test_none_raises_value_error(self):
        with self.assertRaises(ValueError):
            extract_json_object(None)

    def test_no_json_object_raises_value_error(self):
        with self.assertRaises(ValueError):
            extract_json_object("just plain prose, no braces at all")

    def test_malformed_braces_raises_json_decode_error(self):
        """A `{...}` shape that isn't valid JSON inside the braces
        propagates JSONDecodeError so callers can distinguish 'no
        candidate' from 'candidate but unparseable'."""
        with self.assertRaises(json.JSONDecodeError):
            extract_json_object("{invalid}")


if __name__ == "__main__":
    unittest.main()
