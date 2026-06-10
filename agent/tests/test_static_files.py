import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.static_files import resolve_dist_dir, serve_dist


class TestServeDist(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dist = Path(self._tmp.name)
        (self.dist / "index.html").write_bytes(b"<html>swarpius</html>")
        (self.dist / "assets").mkdir()
        (self.dist / "assets" / "app.js").write_bytes(b"console.log('hi')")
        (self.dist / "assets" / "style.css").write_bytes(b"body{}")
        (self.dist / "swarpius-favicon.svg").write_bytes(b"<svg/>")

    def tearDown(self):
        self._tmp.cleanup()

    def test_root_path_serves_index_html(self):
        status, headers, body = serve_dist(self.dist, "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertEqual(body, b"<html>swarpius</html>")

    def test_explicit_index_html(self):
        status, _, body = serve_dist(self.dist, "/index.html")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<html>swarpius</html>")

    def test_nested_asset(self):
        status, headers, body = serve_dist(self.dist, "/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn(headers["Content-Type"], {"application/javascript", "text/javascript"})
        self.assertEqual(body, b"console.log('hi')")

    def test_css_asset_mime(self):
        status, headers, _ = serve_dist(self.dist, "/assets/style.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css")

    def test_svg_asset(self):
        status, headers, _ = serve_dist(self.dist, "/swarpius-favicon.svg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/svg+xml")

    def test_query_string_is_stripped(self):
        status, _, body = serve_dist(self.dist, "/assets/app.js?v=abc123")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"console.log('hi')")

    def test_fragment_is_stripped(self):
        status, _, body = serve_dist(self.dist, "/index.html#top")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<html>swarpius</html>")

    def test_spa_fallback_for_extensionless_path(self):
        status, headers, body = serve_dist(self.dist, "/chat")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertEqual(body, b"<html>swarpius</html>")

    def test_missing_asset_returns_404(self):
        status, _, _ = serve_dist(self.dist, "/assets/missing.js")
        self.assertEqual(status, 404)

    def test_path_traversal_blocked(self):
        # Create a sibling file outside dist to confirm we don't read it.
        outside = self.dist.parent / "secret.txt"
        outside.write_bytes(b"secret")
        try:
            status, _, _ = serve_dist(self.dist, "/../secret.txt")
            self.assertEqual(status, 404)
        finally:
            outside.unlink()

    def test_content_length_matches_body(self):
        status, headers, body = serve_dist(self.dist, "/assets/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(body))


class TestResolveDistDir(unittest.TestCase):
    def test_returns_none_when_no_dist_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Force the source-layout branch by stubbing __file__'s parent
            # chain to a directory that has no web-client/dist sibling.
            fake_app_dir = Path(tmp) / "app"
            fake_app_dir.mkdir()
            with patch("app.io.static_files.__file__", str(fake_app_dir / "static_files.py")), \
                 patch("app.io.static_files.sys") as mock_sys:
                mock_sys._MEIPASS = None
                # getattr(mock_sys, "_MEIPASS", None) → MagicMock, so be explicit:
                del mock_sys._MEIPASS
                result = resolve_dist_dir()
                self.assertIsNone(result)

    def test_finds_meipass_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            meipass = Path(tmp)
            (meipass / "web-client" / "dist").mkdir(parents=True)
            (meipass / "web-client" / "dist" / "index.html").write_bytes(b"<html/>")
            with patch("app.io.static_files.sys") as mock_sys:
                mock_sys._MEIPASS = str(meipass)
                result = resolve_dist_dir()
            self.assertEqual(result, meipass / "web-client" / "dist")


if __name__ == "__main__":
    unittest.main()
