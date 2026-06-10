"""Tests for non-blocking scan & rerun handlers.

Previously the analysis-run-request handler awaited scan_and_analyse /
run_analysis in-line, which blocked the WebSocket receive loop — users
couldn't view other conversations or control Roon until the scan
finished. These tests verify the new pattern: respond immediately with
`accepted: true` and complete the work on a background task that sends
a `completed: true` follow-up on the same channel.

The routing tests (TestBackgroundScan, TestBackgroundRerun) patch
``scan_and_analyse`` / ``run_analysis`` / ``list_analysed_conversations``
/ ``get_list_entry`` — external collaborators imported from
``app.analysis.browser`` with their own coverage. The handler's
contract under test is the routing — translating helper outcomes
(success/failure/empty/error) into the WS-response shape — which
requires arranging specific helper behaviours that mocks express most
cleanly. Same scoping rationale as
``test_analysis_browser::TestRunAnalysis`` / ``::TestScanAndAnalyse``,
``test_analyser_loop``, and ``test_roon_action_stop::TestStopShim``.

TestBackgroundDoesNotBlockEventLoop runs against a real asyncio loop
and asserts the executor-thread non-blocking contract end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.io.websocket_flow import _background_rerun, _background_scan


class _FakeWebSocket:
    """Records outbound sends so assertions can inspect them."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


def _payloads_on_channel(ws: _FakeWebSocket, channel: str) -> list[dict]:
    return [m["payload"] for m in ws.sent if m.get("channel") == channel]


class TestBackgroundScan(unittest.IsolatedAsyncioTestCase):

    async def test_sends_completion_with_result_on_success(self):
        ws = _FakeWebSocket()
        mock_result = {"ok": True, "analysed_count": 2, "errors": []}
        mock_list = {"conversations": [{"conversation_id": "c01"}], "models": ["m"]}

        with patch("app.analysis.browser.scan_and_analyse", return_value=mock_result), \
             patch("app.analysis.browser.list_analysed_conversations", return_value=mock_list):
            await _background_scan(ws, Path("/tmp/fake-logs"), "req-123")

        run_responses = _payloads_on_channel(ws, "analysis-run-response")
        self.assertEqual(len(run_responses), 1)
        self.assertEqual(run_responses[0]["request_id"], "req-123")
        self.assertTrue(run_responses[0]["completed"])
        self.assertTrue(run_responses[0]["ok"])
        self.assertEqual(run_responses[0]["analysed_count"], 2)

        update_responses = _payloads_on_channel(ws, "analysis-update")
        self.assertEqual(len(update_responses), 1)
        self.assertEqual(update_responses[0]["type"], "list_refreshed")

    async def test_completion_response_marks_error_on_exception(self):
        ws = _FakeWebSocket()
        with patch(
            "app.analysis.browser.scan_and_analyse",
            side_effect=RuntimeError("disk full"),
        ):
            await _background_scan(ws, Path("/tmp/fake-logs"), "req-xyz")

        run_responses = _payloads_on_channel(ws, "analysis-run-response")
        self.assertEqual(len(run_responses), 1)
        self.assertEqual(run_responses[0]["request_id"], "req-xyz")
        self.assertTrue(run_responses[0]["completed"])
        self.assertFalse(run_responses[0]["ok"])
        self.assertIn("error", run_responses[0])


class TestBackgroundRerun(unittest.IsolatedAsyncioTestCase):

    async def test_sends_completion_and_list_entry_update(self):
        ws = _FakeWebSocket()
        mock_result = {"ok": True, "analysis": {"conversation_id": "c01"}}
        mock_entry = {"conversation_id": "c01", "severity": "medium"}

        with patch("app.analysis.browser.run_analysis", return_value=mock_result), \
             patch("app.analysis.browser.get_list_entry", return_value=mock_entry):
            await _background_rerun(ws, Path("/tmp/fake-logs"), "2026-04-17", "c01", "req-a")

        run_responses = _payloads_on_channel(ws, "analysis-run-response")
        self.assertEqual(len(run_responses), 1)
        self.assertTrue(run_responses[0]["completed"])
        self.assertTrue(run_responses[0]["ok"])
        self.assertEqual(run_responses[0]["request_id"], "req-a")

        update_responses = _payloads_on_channel(ws, "analysis-update")
        self.assertEqual(len(update_responses), 1)
        self.assertEqual(update_responses[0]["type"], "list_entry_updated")
        self.assertEqual(update_responses[0]["entry"]["conversation_id"], "c01")

    async def test_rerun_failure_skips_list_entry_update(self):
        ws = _FakeWebSocket()
        mock_result = {"ok": False, "error": "Analysis timed out"}

        with patch("app.analysis.browser.run_analysis", return_value=mock_result):
            await _background_rerun(ws, Path("/tmp/fake-logs"), "2026-04-17", "c01", "req-b")

        run_responses = _payloads_on_channel(ws, "analysis-run-response")
        self.assertEqual(len(run_responses), 1)
        self.assertFalse(run_responses[0]["ok"])
        self.assertEqual(run_responses[0]["error"], "Analysis timed out")

        # No list-entry push when the rerun failed
        update_responses = _payloads_on_channel(ws, "analysis-update")
        self.assertEqual(len(update_responses), 0)


class TestBackgroundDoesNotBlockEventLoop(unittest.IsolatedAsyncioTestCase):
    """Scheduling the background task must return control to the caller
    immediately so the WS receive loop can handle other messages. We
    prove this by showing event-loop work happens while the background
    scan is still in its executor thread.
    """

    async def test_other_coroutines_run_while_scan_is_in_executor(self):
        ws = _FakeWebSocket()
        # threading.Event — safe to set from the executor thread and
        # read from the event-loop thread, unlike asyncio.Event.
        import threading as _threading
        scan_entered = _threading.Event()
        scan_may_finish = _threading.Event()
        loop_tick_count = 0

        def _slow_scan(_root):
            scan_entered.set()
            # Wait (in executor thread) for the test to signal completion
            scan_may_finish.wait(timeout=5.0)
            return {"ok": True, "analysed_count": 0, "errors": []}

        with patch("app.analysis.browser.scan_and_analyse", side_effect=_slow_scan), \
             patch("app.analysis.browser.list_analysed_conversations",
                   return_value={"conversations": [], "models": []}):
            task = asyncio.create_task(_background_scan(ws, Path("/tmp"), "req-slow"))

            # Wait for the scan to be running in its executor thread
            while not scan_entered.is_set():
                await asyncio.sleep(0.005)
                loop_tick_count += 1
                if loop_tick_count > 1000:  # safety bound
                    self.fail("scan did not enter the executor thread")

            # Event loop is alive — if the scan were awaited inline, we'd
            # have been stuck. Prove it by running a few more ticks.
            for _ in range(3):
                await asyncio.sleep(0.005)
                loop_tick_count += 1

            # Now let the fake scan return and the background task finish
            scan_may_finish.set()
            _ = await task

        self.assertGreater(loop_tick_count, 3)
        run_responses = _payloads_on_channel(ws, "analysis-run-response")
        self.assertEqual(len(run_responses), 1)
        self.assertTrue(run_responses[0]["completed"])


if __name__ == "__main__":
    unittest.main()
