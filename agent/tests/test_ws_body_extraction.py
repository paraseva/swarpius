"""_extract_body — body coercion for inbound WS frames.

Contract: an explicit (stripped, non-empty) ``body`` wins; otherwise a
string ``payload`` (stripped); otherwise a dict/list ``payload`` is JSON-
encoded; otherwise "" (the caller skips empty bodies). This guards against
silently dropping structured (payload-dict) requests.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.io.websocket_flow import _extract_body  # noqa: E402


class TestExtractBody(unittest.TestCase):
    def test_explicit_body_is_used_and_stripped(self):
        self.assertEqual(_extract_body({"body": "  hello  "}), "hello")

    def test_explicit_body_wins_over_payload(self):
        self.assertEqual(_extract_body({"body": "a", "payload": "b"}), "a")

    def test_string_payload_used_when_no_body(self):
        self.assertEqual(_extract_body({"payload": "  hi  "}), "hi")

    def test_dict_payload_is_json_encoded(self):
        self.assertEqual(_extract_body({"payload": {"x": 1}}), json.dumps({"x": 1}))

    def test_list_payload_is_json_encoded(self):
        self.assertEqual(_extract_body({"payload": [1, 2]}), json.dumps([1, 2]))

    def test_empty_when_nothing_usable(self):
        self.assertEqual(_extract_body({}), "")
        self.assertEqual(_extract_body({"body": "   "}), "")
        self.assertEqual(_extract_body({"payload": 42}), "")


if __name__ == "__main__":
    unittest.main()
