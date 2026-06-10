"""Tests for the analyser's halt-on-permanent-error behaviour.

The API key is resolved once at startup, so a permanent error (auth,
misconfig) will fail identically every subsequent call. Looping
forever on a bad key is noise — `_handle_llm_failure` should log the
underlying provider message and raise ``AnalyserFatalError`` so the
agent's in-process call path can surface the failure to the UI
(or the CLI ``main()`` can translate it to a non-zero exit code).
"""

import unittest
from unittest.mock import patch

from analyser.analyse import AnalyserFatalError, _handle_llm_failure
from analyser.llm_layer import CompletionResult


class TestHandleLlmFailure(unittest.TestCase):

    def test_permanent_error_raises_fatal(self):
        completion = CompletionResult(
            text=None, error_kind="permanent",
            detail="AuthenticationError: invalid x-api-key",
        )
        with self.assertRaises(AnalyserFatalError):
            _handle_llm_failure(completion, "2026-05-11/c01")

    def test_transient_error_does_not_exit(self):
        """Transient errors (rate limits, timeouts) should be retried,
        not halted on."""
        completion = CompletionResult(
            text=None, error_kind="transient",
            detail="429 rate limit exceeded",
        )
        # Should return without raising.
        _handle_llm_failure(completion, "2026-05-11/c01")

    def test_input_shape_error_does_not_exit(self):
        """Input-shape errors (context too large) need caller-side
        handling (truncate, batch smaller), not a halt."""
        completion = CompletionResult(
            text=None, error_kind="input_shape",
            detail="ContextLengthExceeded",
        )
        _handle_llm_failure(completion, "2026-05-11/c01")

    def test_unknown_error_kind_does_not_exit(self):
        completion = CompletionResult(text=None, error_kind=None, detail="???")
        _handle_llm_failure(completion, "2026-05-11/c01")

    def test_detail_included_in_log_message(self):
        completion = CompletionResult(
            text=None, error_kind="transient",
            detail="connection reset",
        )
        with patch("analyser.analyse.log") as mock_log:
            _handle_llm_failure(completion, "ctx")
            args, _ = mock_log.error.call_args
            rendered = args[0] % args[1:]
            self.assertIn("transient", rendered)
            self.assertIn("ctx", rendered)
            self.assertIn("connection reset", rendered)

    def test_missing_detail_omitted_cleanly(self):
        """No detail string shouldn't render as ': None' or similar."""
        completion = CompletionResult(text=None, error_kind="transient", detail=None)
        with patch("analyser.analyse.log") as mock_log:
            _handle_llm_failure(completion, "ctx")
            args, _ = mock_log.error.call_args
            rendered = args[0] % args[1:]
            self.assertNotIn("None", rendered)


if __name__ == "__main__":
    unittest.main()
