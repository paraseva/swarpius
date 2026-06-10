"""Behaviour contract for ``fetch_image_bytes``.

Targets the visible contract (api.get_image variants, HTTP
fallback, auth header, error path) rather than internal call order.
The only boundary stubbed is the function's only boundary: a Roon
API instance and the ``requests.get`` HTTP call.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.exceptions import ExternalServiceError
from roon_core.image_fetch import fetch_image_bytes


def _call(api, image_key: str, width: int = 400, height: int = 400, *, host: str = "core.local", port: int = 9100):
    return fetch_image_bytes(api, host, port, image_key, width, height)


def _response(*, status: int = 200, content: bytes = b"image-bytes", content_type: str = "image/png"):
    resp = SimpleNamespace()
    resp.status_code = status
    resp.content = content
    resp.headers = {"Content-Type": content_type}
    return resp


class TestInputValidation(unittest.TestCase):
    def test_empty_image_key_raises_value_error(self):
        api = SimpleNamespace()
        with self.assertRaises(ValueError):
            _call(api, "")


class TestApiGetImagePath(unittest.TestCase):
    """When ``api.get_image`` is callable, it's tried first with a
    sequence of kwarg variants; the function picks the first variant
    whose signature accepts and returns useful data."""

    def test_returns_bytes_with_default_mime_when_api_returns_raw_bytes(self):
        def get_image(image_key, width, height):
            return b"raw-bytes"
        api = SimpleNamespace(get_image=get_image)
        data, mime = _call(api, "img-1")
        self.assertEqual(data, b"raw-bytes")
        self.assertEqual(mime, "image/jpeg")

    def test_returns_api_provided_mime_when_api_returns_tuple(self):
        def get_image(image_key, width, height):
            return (b"png-bytes", "image/png")
        api = SimpleNamespace(get_image=get_image)
        data, mime = _call(api, "img-2")
        self.assertEqual(data, b"png-bytes")
        self.assertEqual(mime, "image/png")

    def test_falls_through_when_api_get_image_raises(self):
        """If ``api.get_image`` raises for every variant, the HTTP
        fallback path takes over."""
        def get_image(**_kwargs):
            raise RuntimeError("api broken")
        api = SimpleNamespace(get_image=get_image, token=None)
        with patch("roon_core.image_fetch.requests.get") as mock_get:
            mock_get.return_value = _response(content=b"http-bytes")
            data, _mime = _call(api, "img-3")
        self.assertEqual(data, b"http-bytes")


class TestHttpFallbackPath(unittest.TestCase):
    """When ``api.get_image`` is missing, the function calls Roon's
    image endpoint over HTTP directly. Used by lifecycle paths
    where the roonapi instance doesn't include a ``get_image``."""

    def test_calls_first_url_variant_with_width_height_query(self):
        api = SimpleNamespace(token=None)
        with patch("roon_core.image_fetch.requests.get") as mock_get:
            mock_get.return_value = _response()
            _call(api, "img-4", width=300, height=300, host="roon", port=9100)
        first_url = mock_get.call_args_list[0][0][0]
        self.assertIn("http://roon:9100/api/image/img-4", first_url)
        self.assertIn("width=300", first_url)
        self.assertIn("height=300", first_url)

    def test_returns_content_type_from_response_header(self):
        api = SimpleNamespace(token=None)
        with patch("roon_core.image_fetch.requests.get") as mock_get:
            mock_get.return_value = _response(content=b"img-5-bytes", content_type="image/webp")
            data, mime = _call(api, "img-5")
        self.assertEqual(data, b"img-5-bytes")
        self.assertEqual(mime, "image/webp")

    def test_includes_bearer_authorization_header_when_token_present(self):
        api = SimpleNamespace(token="secret-token")
        with patch("roon_core.image_fetch.requests.get") as mock_get:
            mock_get.return_value = _response()
            _call(api, "img-6")
        kwargs = mock_get.call_args_list[0][1]
        self.assertEqual(kwargs["headers"].get("Authorization"), "Bearer secret-token")

    def test_advances_through_url_variants_until_one_succeeds(self):
        """The first two variants 500, the third 200 — the function
        keeps trying and returns the third variant's content."""
        api = SimpleNamespace(token=None)
        with patch("roon_core.image_fetch.requests.get") as mock_get:
            mock_get.side_effect = [
                _response(status=500, content=b""),
                _response(status=500, content=b""),
                _response(status=200, content=b"third-variant-bytes"),
            ]
            data, _mime = _call(api, "img-7")
        self.assertEqual(data, b"third-variant-bytes")
        self.assertEqual(mock_get.call_count, 3)


class TestFailureMode(unittest.TestCase):
    def test_raises_external_service_error_when_all_paths_fail(self):
        api = SimpleNamespace(token=None)
        with patch("roon_core.image_fetch.requests.get") as mock_get:
            mock_get.return_value = _response(status=500, content=b"")
            with self.assertRaises(ExternalServiceError) as ctx:
                _call(api, "img-8")
        self.assertIn("img-8", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
