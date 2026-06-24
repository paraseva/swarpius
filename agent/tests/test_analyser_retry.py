"""_complete_with_retry: bounded backoff on transient errors only.

Transient failures (rate limit, timeout) are retried up to a fixed number of
attempts; permanent and input-shape failures return immediately — retrying
won't help. The boundary (llm_completion) is mocked and sleep is patched out.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analyser import analyse  # noqa: E402
from analyser.llm_layer import CompletionResult  # noqa: E402


def _call():
    return analyse._complete_with_retry("anthropic/x", "key", "sys", "user", 4096)


class TestCompleteWithRetry(unittest.TestCase):
    def test_transient_is_retried_to_the_attempt_limit(self):
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return CompletionResult(text=None, error_kind="transient", detail="429")

        with patch.object(analyse, "llm_completion", side_effect=fake), \
             patch.object(analyse, "_sleep"):
            result = _call()
        self.assertEqual(len(calls), analyse._RETRY_ATTEMPTS)
        self.assertEqual(result.error_kind, "transient")

    def test_transient_then_success_stops_retrying(self):
        seq = [
            CompletionResult(text=None, error_kind="transient"),
            CompletionResult(text=None, error_kind="transient"),
            CompletionResult(text="ok"),
        ]
        with patch.object(analyse, "llm_completion", side_effect=seq), \
             patch.object(analyse, "_sleep"):
            result = _call()
        self.assertEqual(result.text, "ok")

    def test_input_shape_is_not_retried(self):
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return CompletionResult(text=None, error_kind="input_shape")

        with patch.object(analyse, "llm_completion", side_effect=fake), \
             patch.object(analyse, "_sleep"):
            _call()
        self.assertEqual(len(calls), 1)

    def test_permanent_is_not_retried(self):
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return CompletionResult(text=None, error_kind="permanent")

        with patch.object(analyse, "llm_completion", side_effect=fake), \
             patch.object(analyse, "_sleep"):
            _call()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
