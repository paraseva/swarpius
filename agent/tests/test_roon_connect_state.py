"""Roon state delivered to clients on connect and after pairing.

Two contracts that keep the first-run / restart experience clean:
- The connect handler must not report Core "lost" before pairing — that
  would flash the mid-session "Reconnecting to your Roon Core" overlay.
- Once pairing completes, the default zone (and a zone snapshot) is
  re-broadcast, so a client that connected mid-pairing gets the real
  state without a manual refresh.
"""

from __future__ import annotations

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.constants import CHANNEL_DEFAULT_ZONE_UPDATE  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


class _Conn:
    def __init__(self, connected: bool) -> None:
        self.is_connected = connected

    def get_default_zone(self):
        return None


class TestRoonCoreStatusForConnect(unittest.TestCase):
    """Core-status is reported to a connecting client only once paired."""

    def test_none_before_paired(self):
        rt = RuntimeState()
        rt.roon_state = "initialising"
        rt.roon_connection = None
        self.assertIsNone(rt.roon_core_status_for_connect())

    def test_none_before_paired_even_with_a_connection(self):
        rt = RuntimeState()
        rt.roon_state = "awaiting_config"
        rt.roon_connection = _Conn(connected=True)
        self.assertIsNone(rt.roon_core_status_for_connect())

    def test_connected_when_paired_and_linked(self):
        rt = RuntimeState()
        rt.roon_state = "paired"
        rt.roon_connection = _Conn(connected=True)
        self.assertEqual(rt.roon_core_status_for_connect(), "connected")

    def test_lost_when_paired_but_core_down(self):
        rt = RuntimeState()
        rt.roon_state = "paired"
        rt.roon_connection = _Conn(connected=False)
        self.assertEqual(rt.roon_core_status_for_connect(), "lost")


class TestBroadcastRoonReady(unittest.TestCase):
    def test_broadcasts_the_default_zone(self):
        rt = RuntimeState()
        sent: list[str] = []
        rt._ws_send_callback = lambda channel, _payload: sent.append(channel)
        rt.broadcast_roon_ready()
        self.assertIn(CHANNEL_DEFAULT_ZONE_UPDATE, sent)


if __name__ == "__main__":
    unittest.main()
