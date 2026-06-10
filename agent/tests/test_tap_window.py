"""``is_recent`` — does a stored timestamp still fall within the
2-second double-tap window used by the CLI prompt's Ctrl+C exit?"""

from __future__ import annotations

import unittest

from app.cli.tap_window import is_recent


class TestIsRecent(unittest.TestCase):
    def test_no_timestamp_is_not_recent(self) -> None:
        self.assertFalse(is_recent(None, now=10.0))

    def test_within_window_is_recent(self) -> None:
        self.assertTrue(is_recent(timestamp=10.0, now=11.5))

    def test_at_window_boundary_is_recent(self) -> None:
        """Inclusive boundary — a tap at exactly t=window still counts."""
        self.assertTrue(is_recent(timestamp=10.0, now=12.0))

    def test_past_window_is_not_recent(self) -> None:
        self.assertFalse(is_recent(timestamp=10.0, now=12.1))

    def test_custom_window(self) -> None:
        self.assertTrue(is_recent(timestamp=10.0, now=14.9, window_seconds=5.0))
        self.assertFalse(is_recent(timestamp=10.0, now=15.1, window_seconds=5.0))

    def test_default_window_is_two_seconds(self) -> None:
        """Pin the default — the prompt loop relies on it being 2s."""
        self.assertTrue(is_recent(timestamp=0.0, now=1.9))
        self.assertFalse(is_recent(timestamp=0.0, now=2.1))


if __name__ == "__main__":
    unittest.main()
