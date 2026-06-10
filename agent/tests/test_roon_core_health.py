"""``RoonHealthMonitor`` — emits a status event only when the Roon Core
connection *transitions* (connected↔lost). The first poll establishes a
baseline silently so we don't fire a spurious event at startup."""

from __future__ import annotations

import unittest

from app.roon.core_health import RoonHealthMonitor, format_roon_status_message


class TestRoonHealthMonitor(unittest.TestCase):
    def test_first_poll_connected_is_silent_baseline(self):
        m = RoonHealthMonitor()
        self.assertIsNone(m.poll(is_connected=True))

    def test_first_poll_disconnected_is_silent_baseline(self):
        m = RoonHealthMonitor()
        self.assertIsNone(m.poll(is_connected=False))

    def test_no_event_when_state_unchanged(self):
        m = RoonHealthMonitor()
        m.poll(is_connected=True)
        self.assertIsNone(m.poll(is_connected=True))
        self.assertIsNone(m.poll(is_connected=True))

    def test_drop_emits_lost(self):
        m = RoonHealthMonitor()
        m.poll(is_connected=True)
        self.assertEqual(m.poll(is_connected=False), "lost")

    def test_recovery_emits_connected(self):
        m = RoonHealthMonitor()
        m.poll(is_connected=True)
        m.poll(is_connected=False)
        self.assertEqual(m.poll(is_connected=True), "connected")

    def test_full_cycle_emits_only_on_edges(self):
        m = RoonHealthMonitor()
        sequence = [True, True, False, False, True, True]
        events = [m.poll(is_connected=v) for v in sequence]
        # baseline, no-change, drop, no-change, recovery, no-change
        self.assertEqual(events, [None, None, "lost", None, "connected", None])


class TestRoonStatusConsoleMessage(unittest.TestCase):
    """The CLI surfaces Core drops/reconnects as a console line (WS mode
    shows a modal); the user must know why playback stopped working."""

    def test_lost_message_says_lost_and_reconnecting(self):
        msg = format_roon_status_message("lost")
        self.assertIn("Lost connection to Roon Core", msg)
        self.assertIn("reconnecting", msg.lower())

    def test_connected_message_says_reconnected(self):
        msg = format_roon_status_message("connected")
        self.assertIn("reconnected", msg.lower())


if __name__ == "__main__":
    unittest.main()
