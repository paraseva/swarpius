"""The agent's WebSocket server binds to loopback (127.0.0.1) by default, so
source and bundled-app installs aren't reachable from the LAN unless the
operator opts in. Docker sets SWARPIUS_WS_HOST=0.0.0.0 explicitly (the
container must listen broadly for port-publish; the host-side port mapping
keeps it on loopback) — see SECURITY.md.
"""
import os
import unittest
from unittest.mock import patch

from app.settings.core import Settings


class TestWsHostBindDefault(unittest.TestCase):
    def test_defaults_to_loopback_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SWARPIUS_WS_HOST", None)
            assert Settings.from_env().ws_host == "127.0.0.1"

    def test_honours_explicit_bind(self):
        with patch.dict(os.environ, {"SWARPIUS_WS_HOST": "0.0.0.0"}):
            assert Settings.from_env().ws_host == "0.0.0.0"


if __name__ == "__main__":
    unittest.main()
