"""Image-byte fetch + cache flow (the request-response path the
browser uses to retrieve Roon artwork bytes by image_key)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.exceptions import RoonConnectionUnavailableError
from app.runtime.state import RuntimeState
from tests._runtime_fixtures import wire_zone_artwork, wire_zone_domain


def _bare_runtime() -> RuntimeState:
    rs = object.__new__(RuntimeState)
    rs.roon_connection = None
    rs._ws_send_callback = lambda _c, _p: None
    wire_zone_domain(rs)
    wire_zone_artwork(rs)
    return rs


class TestImageBase64Payload(unittest.TestCase):

    def test_raises_when_no_roon_connection(self):
        rs = _bare_runtime()
        with self.assertRaises(RoonConnectionUnavailableError):
            rs.get_image_base64_payload("img-abc")

    def test_fetch_and_cache_roundtrip(self):
        rs = _bare_runtime()
        rs.roon_connection = MagicMock()
        rs.roon_connection.fetch_image_bytes.return_value = (b"raw-bytes", "image/jpeg")

        first = rs.get_image_base64_payload("img-abc", 200, 200)
        self.assertEqual(first["image_key"], "img-abc")
        self.assertEqual(first["mime_type"], "image/jpeg")
        self.assertTrue(first["base64_data"])

        rs.roon_connection.fetch_image_bytes.reset_mock()
        second = rs.get_image_base64_payload("img-abc", 200, 200)
        self.assertEqual(second["base64_data"], first["base64_data"])
        rs.roon_connection.fetch_image_bytes.assert_not_called()

    def test_different_sizes_are_separate_cache_entries(self):
        rs = _bare_runtime()
        rs.roon_connection = MagicMock()
        rs.roon_connection.fetch_image_bytes.side_effect = [
            (b"small-bytes", "image/jpeg"),
            (b"large-bytes", "image/jpeg"),
        ]
        rs.get_image_base64_payload("img-abc", 200, 200)
        rs.get_image_base64_payload("img-abc", 800, 800)
        self.assertEqual(rs.roon_connection.fetch_image_bytes.call_count, 2)


if __name__ == "__main__":
    unittest.main()
