"""Regression test: add_feedback_item writes a tz-aware timestamp.

passive-analyser's feedback.py used `datetime.now().isoformat()` (naive),
while the agent side (`analysis_feedback.py:118`) uses UTC-aware. The
asymmetry is silent today because `add_feedback_item` has no production
callers — but if anyone ever compares timestamps across both paths, or
if a path migrates to call this writer, naive vs aware comparison
raises TypeError.

Pin the contract: analyser-side timestamps are UTC-aware ISO strings.
"""

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from analyser.feedback import add_feedback_item  # noqa: E402


class TestAddFeedbackItemTimezone(unittest.TestCase):

    def test_timestamp_is_utc_aware_isoformat(self) -> None:
        """Round-trip through datetime.fromisoformat must yield a
        tzinfo-carrying value.
        """
        with TemporaryDirectory() as tmp:
            item = add_feedback_item(
                Path(tmp),
                request_id="rq-c01-0001",
                failure_mode="FM-09",
                disposition="dismiss",
                rebuttal="looks wrong to me",
            )

            parsed = datetime.fromisoformat(item["timestamp"])
            self.assertIsNotNone(
                parsed.tzinfo,
                "add_feedback_item wrote a naive timestamp; expected "
                "UTC-aware to match the agent-side writer.",
            )


if __name__ == "__main__":
    unittest.main()
