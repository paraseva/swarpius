"""Session-level usage aggregator for CLI mode.

Pins the contract that:
  * ``accumulate`` adds per-request usage into running totals
    (tokens, cache reads, cost, steps, duration, request count).
  * Missing usage keys are treated as zero — same tolerant
    semantics as the per-request formatter.
  * ``format_summary`` renders the one-liner that prints after
    each response: tokens / cache / cost / request count /
    elapsed. Zero/missing fields are suppressed so an idle
    session reads cleanly once data arrives.
  * ``format_detailed`` (used by ``/usage``) adds a per-request
    average line so the operator can see typical request cost.
  * ``has_data`` gates whether there's anything to report
    (empty session → ``/usage`` should say so explicitly).
"""

from __future__ import annotations

import unittest

from app.cli.session_usage import SessionUsageTracker


class TestAccumulate(unittest.TestCase):
    def test_first_accumulate_initialises_totals(self) -> None:
        s = SessionUsageTracker()
        self.assertFalse(s.has_data())
        s.accumulate(
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 50,
                "cost_usd": 0.001,
            },
            steps=2,
            duration_ms=500,
        )
        self.assertTrue(s.has_data())
        self.assertEqual(s.input_tokens, 100)
        self.assertEqual(s.output_tokens, 20)
        self.assertEqual(s.cache_read, 50)
        self.assertAlmostEqual(s.cost_usd, 0.001)
        self.assertEqual(s.steps, 2)
        self.assertEqual(s.duration_ms, 500)
        self.assertEqual(s.request_count, 1)

    def test_repeated_accumulate_sums(self) -> None:
        s = SessionUsageTracker()
        s.accumulate(usage={"input_tokens": 100, "output_tokens": 10}, steps=1, duration_ms=200)
        s.accumulate(usage={"input_tokens": 200, "output_tokens": 20}, steps=2, duration_ms=300)
        self.assertEqual(s.input_tokens, 300)
        self.assertEqual(s.output_tokens, 30)
        self.assertEqual(s.steps, 3)
        self.assertEqual(s.duration_ms, 500)
        self.assertEqual(s.request_count, 2)

    def test_missing_keys_treated_as_zero(self) -> None:
        s = SessionUsageTracker()
        s.accumulate(usage={}, steps=1, duration_ms=100)
        self.assertEqual(s.input_tokens, 0)
        self.assertEqual(s.cost_usd, 0.0)
        self.assertEqual(s.request_count, 1)


class TestFormatSummary(unittest.TestCase):
    def test_multiple_requests(self) -> None:
        s = SessionUsageTracker()
        for _ in range(5):
            s.accumulate(
                usage={"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.002},
                steps=2, duration_ms=1500,
            )
        line = s.format_summary()
        self.assertIn("5,000 in", line)
        self.assertIn("250 out", line)
        self.assertIn("$0.0100", line)
        self.assertIn("5 requests", line)
        self.assertIn("7.5s", line)

    def test_zero_fields_suppressed(self) -> None:
        s = SessionUsageTracker()
        s.accumulate(usage={"input_tokens": 100, "output_tokens": 20}, steps=1, duration_ms=200)
        line = s.format_summary()
        self.assertNotIn("cached", line)
        self.assertNotIn("$", line)


class TestFormatDetailed(unittest.TestCase):
    def test_includes_per_request_averages(self) -> None:
        s = SessionUsageTracker()
        for _ in range(4):
            s.accumulate(
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cache_read_input_tokens": 800,
                    "cost_usd": 0.004,
                },
                steps=2, duration_ms=2000,
            )
        text = s.format_detailed()
        # Totals on first line
        self.assertIn("4,000 in", text)
        self.assertIn("4 requests", text)
        # Averages on second line — 1000 in / 100 out / 800 cached / 0.004 cost per request
        self.assertIn("1,000 in", text)
        self.assertIn("100 out", text)
        self.assertIn("800 cached", text)
        self.assertIn("$0.0040", text)
        self.assertIn("per request", text)

    def test_no_average_line_for_single_request(self) -> None:
        """Average-of-one is just the request itself — redundant."""
        s = SessionUsageTracker()
        s.accumulate(usage={"input_tokens": 100, "output_tokens": 20}, steps=1, duration_ms=200)
        text = s.format_detailed()
        self.assertNotIn("per request", text)


class TestHasData(unittest.TestCase):
    def test_starts_false(self) -> None:
        self.assertFalse(SessionUsageTracker().has_data())

    def test_true_after_any_accumulate(self) -> None:
        s = SessionUsageTracker()
        s.accumulate(usage={}, steps=0, duration_ms=0)
        self.assertTrue(s.has_data())


if __name__ == "__main__":
    unittest.main()
