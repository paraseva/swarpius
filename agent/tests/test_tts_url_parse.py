"""Tests for the TTS_URL parser.

The canonical form is scheme-less ``host:port``. Any leading
``scheme://`` is stripped silently, so ``tcp://``, ``http://``,
``ws://``, etc. all resolve to the same TCP endpoint.
"""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from tts.url import TtsUrlError, parse_tts_url


class TestSchemeless(unittest.TestCase):
    def test_host_and_port(self):
        self.assertEqual(parse_tts_url("localhost:9998"), ("localhost", 9998))

    def test_ipv4(self):
        self.assertEqual(parse_tts_url("192.168.1.50:9998"), ("192.168.1.50", 9998))

    def test_whitespace_tolerated(self):
        self.assertEqual(
            parse_tts_url("  localhost:9998  "),
            ("localhost", 9998),
        )


class TestSchemeStripped(unittest.TestCase):
    """Any leading scheme is stripped silently — F5-TTS only speaks
    TCP, so what the user wrote in front of host:port is irrelevant."""

    def test_tcp_scheme(self):
        self.assertEqual(parse_tts_url("tcp://localhost:9998"), ("localhost", 9998))

    def test_tcp_scheme_case_insensitive(self):
        self.assertEqual(parse_tts_url("TCP://localhost:9998"), ("localhost", 9998))

    def test_http_scheme(self):
        self.assertEqual(parse_tts_url("http://localhost:9998"), ("localhost", 9998))

    def test_ws_scheme(self):
        self.assertEqual(parse_tts_url("ws://localhost:9998"), ("localhost", 9998))


class TestMalformed(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(TtsUrlError):
            parse_tts_url("")

    def test_no_port(self):
        with self.assertRaises(TtsUrlError) as ctx:
            parse_tts_url("localhost")
        self.assertIn("port", str(ctx.exception).lower())

    def test_non_numeric_port(self):
        with self.assertRaises(TtsUrlError):
            parse_tts_url("localhost:abc")

    def test_port_out_of_range(self):
        with self.assertRaises(TtsUrlError):
            parse_tts_url("localhost:99999")

    def test_negative_port(self):
        with self.assertRaises(TtsUrlError):
            parse_tts_url("localhost:-1")


if __name__ == "__main__":
    unittest.main()
