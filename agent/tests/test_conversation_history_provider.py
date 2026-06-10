"""Tests for ConversationHistoryProvider rendering.

The provider exposes past user/agent turns to the coordinator inside
the system prompt. Each turn carries a timestamp so the LLM can see
*when* something was said — without that signal, stale claims like
"Headphones is now your default zone" from an old turn look identical
in weight to live state. Timestamped narrative transcript makes
staleness visible and reads less like authoritative state than the
previous JSON-array dump did.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.coordinator.context_providers import ConversationHistoryProvider


def _at(ts_str: str) -> datetime:
    """Parse YYYY-MM-DD HH:MM into a datetime."""
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M")


class TestEmpty(unittest.TestCase):
    def test_empty_history_returns_empty_string(self):
        provider = ConversationHistoryProvider("Conversation History")
        self.assertEqual(provider.get_info(), "")


class TestNarrativeFormat(unittest.TestCase):
    """Output is a timestamped transcript, not JSON."""

    def test_single_turn_renders_as_user_then_agent(self):
        provider = ConversationHistoryProvider("Conversation History")
        with patch("app.coordinator.context_providers.datetime") as mock_dt:
            mock_dt.now.return_value = _at("2026-05-25 14:00")
            mock_dt.side_effect = datetime
            provider.add_turn("hi", "hello")
            mock_dt.now.return_value = _at("2026-05-25 14:00")
            out = provider.get_info()
        self.assertIn("User: hi", out)
        self.assertIn("Swarpius: hello", out)

    def test_output_is_not_json(self):
        """Regression: the previous implementation dumped raw JSON.
        The narrative form should not contain the JSON-key signature
        ('"user":' / '"agent":') or parse as JSON."""
        import json as _json

        provider = ConversationHistoryProvider("Conversation History")
        provider.add_turn("hi", "hello")
        out = provider.get_info()
        self.assertNotIn('"user":', out)
        self.assertNotIn('"agent":', out)
        with self.assertRaises(_json.JSONDecodeError):
            _json.loads(out)

    def test_turn_carries_relative_and_absolute_timestamp(self):
        provider = ConversationHistoryProvider("Conversation History")
        with patch("app.coordinator.context_providers.datetime") as mock_dt:
            mock_dt.now.return_value = _at("2026-05-25 01:35")
            provider.add_turn("u1", "a1")
            mock_dt.now.return_value = _at("2026-05-25 13:35")  # 12h later
            out = provider.get_info()
        # Absolute timestamp from when the turn happened
        self.assertIn("2026-05-25 01:35", out)
        # Relative time from "now"
        self.assertIn("12 hr ago", out)

    def test_recent_turn_renders_just_now(self):
        provider = ConversationHistoryProvider("Conversation History")
        with patch("app.coordinator.context_providers.datetime") as mock_dt:
            mock_dt.now.return_value = _at("2026-05-25 14:00")
            provider.add_turn("u1", "a1")
            # Same "now" → 0 seconds ago
            out = provider.get_info()
        self.assertIn("just now", out)

    def test_multiple_turns_separated_oldest_first(self):
        provider = ConversationHistoryProvider("Conversation History")
        with patch("app.coordinator.context_providers.datetime") as mock_dt:
            mock_dt.now.return_value = _at("2026-05-25 01:30")
            provider.add_turn("u1", "a1")
            mock_dt.now.return_value = _at("2026-05-25 14:00")
            provider.add_turn("u2", "a2")
            out = provider.get_info()
        idx1 = out.find("User: u1")
        idx2 = out.find("User: u2")
        self.assertGreaterEqual(idx1, 0)
        self.assertGreaterEqual(idx2, 0)
        self.assertLess(idx1, idx2, "older turn should render first")

    def test_max_turns_bound_preserved(self):
        """The deque maxlen bound still applies — adding more turns
        than the max drops the oldest."""
        provider = ConversationHistoryProvider("Conversation History", max_turns=2)
        provider.add_turn("u1", "a1")
        provider.add_turn("u2", "a2")
        provider.add_turn("u3", "a3")
        out = provider.get_info()
        self.assertNotIn("u1", out)
        self.assertIn("u2", out)
        self.assertIn("u3", out)


class TestRelativeTimeBuckets(unittest.TestCase):
    """The relative-time bucket boundaries should match the established
    zone_formatting convention so the LLM sees one consistent
    'X ago' vocabulary across context blocks."""

    def _render(self, age: timedelta) -> str:
        provider = ConversationHistoryProvider("Conversation History")
        with patch("app.coordinator.context_providers.datetime") as mock_dt:
            mock_dt.now.return_value = _at("2026-05-25 14:00")
            provider.add_turn("u", "a")
            mock_dt.now.return_value = _at("2026-05-25 14:00") + age
            return provider.get_info()

    def test_under_one_minute_is_just_now(self):
        self.assertIn("just now", self._render(timedelta(seconds=30)))

    def test_minutes_bucket(self):
        self.assertIn("5 min ago", self._render(timedelta(minutes=5)))

    def test_hours_bucket(self):
        self.assertIn("3 hr ago", self._render(timedelta(hours=3)))

    def test_days_bucket(self):
        self.assertIn("2 days ago", self._render(timedelta(days=2)))

    def test_single_day_uses_singular(self):
        self.assertIn("1 day ago", self._render(timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
