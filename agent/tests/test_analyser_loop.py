"""Tests for the analyser background-loop daemon thread.

Scope: the loop's contract — shutdown timing, fatal-vs-transient
exception handling, settings passthrough. NOT the analyser code the
loop invokes, which has its own unit tests
(``test_analyser_*``, ``test_analyse_*``).

The functions patched here (``prepare_context``, ``acquire_scan_lock``,
``process_all_pending_feedback``, ``consolidate_lessons``, ``run_scan``,
``collect_metrics``) are imported from ``analyser.analyse``, a sibling
module to ``analyser.loop``. From the loop's point of view they are
external collaborators, not internals — so stubbing them is at the
correct boundary for this test's scope. Asserting on their call counts
verifies the loop's observable behaviour (called once vs not called,
in the correct order on the success path).

The key contracts:

- stop_event.set() makes the loop exit promptly (before the next
  scheduled scan)
- AnalyserFatalError from the analyser exits the loop and does NOT
  retry (since the issue won't fix itself)
- Other exceptions log and continue (transient issues like network)
- The initial-scan delay is respected
- Settings (staleness_minutes, batch_size) are passed through to run_scan
"""

import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from analyser.analyse import AnalyserFatalError
from analyser.loop import _run_loop


class _LockAcquired:
    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False


@contextmanager
def _patch_analyser_calls(prep_result=None, prep_side=None, scan_side=None):
    """Patch the analyser entry points the loop calls."""
    with patch(
        "analyser.analyse.prepare_context",
        return_value=prep_result or ("m", "k", "g", "ref"),
        side_effect=prep_side,
    ) as mock_prep, patch(
        "analyser.analyse.acquire_scan_lock", return_value=_LockAcquired(),
    ), patch(
        "analyser.analyse.process_all_pending_feedback",
    ), patch(
        "analyser.analyse.consolidate_lessons",
    ), patch(
        "analyser.analyse.run_scan", side_effect=scan_side,
    ) as mock_scan, patch(
        "analyser.analyse.collect_metrics",
    ):
        yield mock_prep, mock_scan


class TestLoopShutdown(unittest.TestCase):
    def test_stop_event_set_before_start_returns_immediately(self):
        # Pre-set the event; the loop's first wait should exit at once.
        stop = threading.Event()
        stop.set()
        with _patch_analyser_calls() as (mock_prep, mock_scan), \
             patch("analyser.loop._FIRST_SCAN_DELAY_SECONDS", 30):
            start = time.monotonic()
            _run_loop(stop, interval_minutes=5, staleness_minutes=10, batch_size=3)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 1.0, "loop should exit on pre-set stop")
            mock_prep.assert_not_called()
            mock_scan.assert_not_called()

    def test_stop_event_set_mid_loop_exits_promptly(self):
        # Fast-forward the initial delay; let one scan complete; then
        # set the event from another thread and confirm the loop exits.
        stop = threading.Event()

        def _scan_then_signal_stop(*args, **kwargs):
            # Schedule stop from a side thread so the loop sees it on
            # the next wait().
            threading.Timer(0.05, stop.set).start()

        with _patch_analyser_calls(scan_side=_scan_then_signal_stop), \
             patch("analyser.loop._FIRST_SCAN_DELAY_SECONDS", 0):
            start = time.monotonic()
            _run_loop(stop, interval_minutes=60, staleness_minutes=10, batch_size=3)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 2.0, "loop should exit promptly after stop")


class TestLoopErrorHandling(unittest.TestCase):
    def test_fatal_error_in_prepare_context_exits_loop(self):
        stop = threading.Event()
        with _patch_analyser_calls(
            prep_side=AnalyserFatalError("no API key"),
        ) as (mock_prep, mock_scan), patch(
            "analyser.loop._FIRST_SCAN_DELAY_SECONDS", 0,
        ):
            _run_loop(stop, interval_minutes=60, staleness_minutes=10, batch_size=3)
            # prepare_context called once (then loop exited on fatal)
            self.assertEqual(mock_prep.call_count, 1)
            mock_scan.assert_not_called()

    def test_fatal_error_in_run_scan_exits_loop(self):
        stop = threading.Event()
        with _patch_analyser_calls(
            scan_side=AnalyserFatalError("permanent LLM failure"),
        ) as (mock_prep, mock_scan), patch(
            "analyser.loop._FIRST_SCAN_DELAY_SECONDS", 0,
        ):
            _run_loop(stop, interval_minutes=60, staleness_minutes=10, batch_size=3)
            self.assertEqual(mock_scan.call_count, 1)

    def test_transient_exception_in_run_scan_continues_loop(self):
        """A non-fatal exception (e.g. network blip) should be logged
        and skipped — the loop continues onto the next tick."""
        stop = threading.Event()
        call_count = {"n": 0}

        def _flaky_scan(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("network hiccup")
            # Second call: signal stop so the loop exits cleanly.
            stop.set()

        with _patch_analyser_calls(scan_side=_flaky_scan) as (_, mock_scan), \
             patch("analyser.loop._FIRST_SCAN_DELAY_SECONDS", 0):
            _run_loop(stop, interval_minutes=0, staleness_minutes=10, batch_size=3)
            self.assertEqual(mock_scan.call_count, 2,
                             "loop should retry after a transient error")

    def test_transient_exception_in_prepare_continues_loop(self):
        """Same as above but the exception is in prepare_context."""
        stop = threading.Event()
        call_count = {"n": 0}

        def _flaky_prep(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient")
            stop.set()
            return ("m", "k", "g", "ref")

        with patch(
            "analyser.analyse.prepare_context", side_effect=_flaky_prep,
        ) as mock_prep, patch(
            "analyser.analyse.acquire_scan_lock", return_value=_LockAcquired(),
        ), patch(
            "analyser.analyse.process_all_pending_feedback",
        ), patch(
            "analyser.analyse.consolidate_lessons",
        ), patch(
            "analyser.analyse.run_scan",
        ), patch(
            "analyser.analyse.collect_metrics",
        ), patch(
            "analyser.loop._FIRST_SCAN_DELAY_SECONDS", 0,
        ):
            _run_loop(stop, interval_minutes=0, staleness_minutes=10, batch_size=3)
            self.assertGreaterEqual(mock_prep.call_count, 2,
                                    "loop should retry after a transient prep error")


class TestLoopScanInputs(unittest.TestCase):
    def test_passes_settings_through_to_run_scan(self):
        stop = threading.Event()
        captured = {}

        def _capture_scan(*args, **kwargs):
            captured["args"] = args
            stop.set()

        with _patch_analyser_calls(scan_side=_capture_scan), \
             patch("analyser.loop._FIRST_SCAN_DELAY_SECONDS", 0):
            _run_loop(stop, interval_minutes=15, staleness_minutes=45, batch_size=7)
            # run_scan(model, api_key, guide_text, git_ref, staleness, batch_size)
            self.assertEqual(captured["args"][4], 45)
            self.assertEqual(captured["args"][5], 7)


if __name__ == "__main__":
    unittest.main()
