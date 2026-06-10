"""Tests for WS payload validators that gate path-component strings.

These validators enforce that ``date`` / ``conversation_id`` /
``request_id`` strings arriving over the WebSocket conform to their
documented shape *before* they are used as path components — defending
against directory traversal (``..``), absolute paths, NUL bytes, and
shell-meta characters in any subsequent filesystem operation.
"""

from __future__ import annotations

import unittest

from app.io.ws_path_validators import (
    validate_conversation_id,
    validate_date,
    validate_image_key,
    validate_request_id,
)


class TestValidateDate(unittest.TestCase):

    def test_valid_date_passes(self):
        self.assertEqual(validate_date("2026-04-27"), "2026-04-27")

    def test_strips_whitespace(self):
        self.assertEqual(validate_date("  2026-04-27 "), "2026-04-27")

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            validate_date("../../../etc")

    def test_absolute_path_rejected(self):
        with self.assertRaises(ValueError):
            validate_date("/etc/passwd")

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            validate_date("")

    def test_none_rejected(self):
        with self.assertRaises(ValueError):
            validate_date(None)  # type: ignore[arg-type]

    def test_wrong_format_rejected(self):
        for bad in ("2026/04/27", "26-04-27", "2026-4-27", "2026-04-27extra"):
            with self.assertRaises(ValueError):
                validate_date(bad)

    def test_nul_byte_rejected(self):
        with self.assertRaises(ValueError):
            validate_date("2026-04-27\x00")


class TestValidateConversationId(unittest.TestCase):

    def test_valid_passes(self):
        self.assertEqual(validate_conversation_id("c01"), "c01")
        self.assertEqual(validate_conversation_id("c123"), "c123")

    def test_strips_whitespace(self):
        self.assertEqual(validate_conversation_id("  c02 "), "c02")

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            validate_conversation_id("../etc")

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            validate_conversation_id("")

    def test_wrong_prefix_rejected(self):
        for bad in ("01", "C01", "conv01", "c", "cc01", "c01a"):
            with self.assertRaises(ValueError):
                validate_conversation_id(bad)


class TestValidateRequestId(unittest.TestCase):

    def test_valid_passes(self):
        self.assertEqual(validate_request_id("rq-c01-0001"), "rq-c01-0001")
        self.assertEqual(validate_request_id("rq-c99-9999"), "rq-c99-9999")

    def test_strips_whitespace(self):
        self.assertEqual(validate_request_id("  rq-c01-0001 "), "rq-c01-0001")

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            validate_request_id("../../etc")

    def test_path_separator_rejected(self):
        with self.assertRaises(ValueError):
            validate_request_id("rq-c01-0001/x")

    def test_wrong_format_rejected(self):
        for bad in ("rq-01-0001", "rq-c01", "c01-0001", "rq-c01-", "RQ-C01-0001"):
            with self.assertRaises(ValueError):
                validate_request_id(bad)

class TestValidateImageKey(unittest.TestCase):
    """Roon image keys are opaque alphanumeric strings (with `-`/`_`).

    The agent interpolates them into the Roon Core URL path:
    ``f"{base}/api/image/{image_key}?…"``. Without validation a crafted
    ``image_key`` can escape the path and reach other Roon endpoints.
    """

    def test_valid_alnum_passes(self):
        self.assertEqual(validate_image_key("img_100"), "img_100")
        self.assertEqual(validate_image_key("img-abc-123"), "img-abc-123")

    def test_strips_whitespace(self):
        self.assertEqual(validate_image_key("  img_100  "), "img_100")

    def test_path_separator_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_key("../debug")

    def test_query_injection_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_key("foo?evil=1")

    def test_fragment_injection_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_key("foo#frag")

    def test_ampersand_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_key("foo&extra=1")

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_key("")

    def test_overly_long_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_key("a" * 257)
