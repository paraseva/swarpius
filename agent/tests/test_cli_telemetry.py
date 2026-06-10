"""``format_usage_summary`` renders the per-request token/cost
one-liner that follows the agent's response in CLI mode.

Pins the contract that:
  * Each field is suppressed when the underlying value is zero/None
    (no point adding ``cache 0`` noise to a non-cached run).
  * Token counts use a thousands separator so big runs stay
    readable.
  * Duration auto-scales: sub-second → ``ms``, otherwise → ``Xs``
    with one decimal.
  * Cost rounds to four decimals; below the floor it shows
    ``<$0.0001`` rather than ``$0.0000``.
  * Empty usage with zero everything renders just the steps +
    duration tail (still useful for confirming the CLI reached
    completion even for cached/free runs).
"""

from __future__ import annotations

import unittest

from app.cli.telemetry import format_usage_summary


class TestFormatUsageSummary(unittest.TestCase):
    def test_all_fields_present(self) -> None:
        line = format_usage_summary(
            usage={
                "input_tokens": 1234,
                "output_tokens": 84,
                "cache_read_input_tokens": 800,
                "cost_usd": 0.0034,
            },
            steps=2,
            duration_ms=1430,
        )
        self.assertIn("1,234 in", line)
        self.assertIn("84 out", line)
        self.assertIn("800 cached", line)
        self.assertIn("$0.0034", line)
        self.assertIn("2 steps", line)
        self.assertIn("1.4s", line)

    def test_zero_cache_is_omitted(self) -> None:
        line = format_usage_summary(
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cost_usd": 0.001,
            },
            steps=1,
            duration_ms=200,
        )
        self.assertNotIn("cached", line)
        self.assertIn("100 in", line)

    def test_zero_cost_is_omitted(self) -> None:
        line = format_usage_summary(
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cost_usd": 0.0,
            },
            steps=1,
            duration_ms=200,
        )
        self.assertNotIn("$", line)

    def test_tiny_cost_shows_below_floor_marker(self) -> None:
        line = format_usage_summary(
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": 0.00005,  # rounds to $0.0000
            },
            steps=1,
            duration_ms=50,
        )
        self.assertIn("<$0.0001", line)

    def test_subsecond_duration_uses_ms(self) -> None:
        line = format_usage_summary(
            usage={"input_tokens": 10, "output_tokens": 5},
            steps=1,
            duration_ms=420,
        )
        self.assertIn("420ms", line)
        self.assertNotIn("0.4s", line)

    def test_singular_step(self) -> None:
        line = format_usage_summary(
            usage={"input_tokens": 1, "output_tokens": 1},
            steps=1,
            duration_ms=100,
        )
        self.assertIn("1 step", line)
        self.assertNotIn("1 steps", line)

    def test_missing_keys_treated_as_zero(self) -> None:
        line = format_usage_summary(
            usage={},
            steps=1,
            duration_ms=100,
        )
        # Empty payload still produces something useful — the
        # steps + duration tail tells you the request actually ran.
        self.assertIn("1 step", line)
        self.assertIn("100ms", line)
        self.assertNotIn("in", line.split("·")[0])  # no "X in" leading field


if __name__ == "__main__":
    unittest.main()
