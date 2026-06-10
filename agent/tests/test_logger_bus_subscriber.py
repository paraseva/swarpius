"""``RequestLogger`` subscribes to the AgentEvent bus and writes each
event to ``events.jsonl``. Today's events.jsonl was driven indirectly
from ``WsBroadcaster._send`` (so CLI mode silently lost the log). With
the logger as a direct bus subscriber, events.jsonl is populated in
every transport.

These tests exercise the logger handler in isolation: write events to
a real RequestLogger backed by a temp directory, then read the
events.jsonl back and assert on the captured sequence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.coordinator.events import (
    LlmCallStarted,
    RequestCompleted,
    RequestStarted,
    ToolCompleted,
    ToolStarted,
)


def _make_logger(tmp_root: Path) -> Any:
    from app.runtime.request_logger import RequestLogger
    with patch.dict("os.environ", {"SWARPIUS_DATA_DIR": str(tmp_root)}, clear=False):
        # RequestLogger reads SWARPIUS_DATA_DIR at construction time.
        return RequestLogger("rq-c01-0001")


class TestLoggerBusSubscriber(unittest.TestCase):
    def test_handle_writes_one_jsonl_line_per_event(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logger = _make_logger(Path(td))
            logger.handle(RequestStarted(
                request_id="rq-c01-0001",
                emitted_at_ms=1000,
                user_input="hi",
                coordinator_model="dummy/dummy-model",
                run_mode_label="cli",
            ))
            logger.handle(LlmCallStarted(
                request_id="rq-c01-0001",
                emitted_at_ms=1100,
                call_id="rq-c01-0001-step1",
                step=1,
                agent_name="Coordinator",
                model="dummy/dummy-model",
                prompt_tokens_estimated=0,
                prompt_diagnostics={},
            ))

            events_path = logger.request_dir / "events.jsonl"
            lines = events_path.read_text().strip().split("\n")
            self.assertEqual(len(lines), 2)
            row0 = json.loads(lines[0])
            self.assertEqual(row0["event_type"], "RequestStarted")
            self.assertEqual(row0["payload"]["user_input"], "hi")
            row1 = json.loads(lines[1])
            self.assertEqual(row1["event_type"], "LlmCallStarted")
            self.assertEqual(row1["payload"]["step"], 1)

    def test_handle_records_full_lifecycle_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logger = _make_logger(Path(td))
            logger.handle(RequestStarted(
                request_id="rq-c01-0001",
                emitted_at_ms=0,
                user_input="play music",
                coordinator_model="m",
                run_mode_label="ws",
            ))
            logger.handle(LlmCallStarted(
                request_id="rq-c01-0001",
                emitted_at_ms=10,
                call_id="rq-c01-0001-step1",
                step=1,
                agent_name="Coordinator",
                model="m",
                prompt_tokens_estimated=0,
                prompt_diagnostics={},
            ))
            logger.handle(ToolStarted(
                request_id="rq-c01-0001",
                emitted_at_ms=20,
                tool_call_id="A",
                tool_name="roon_search",
                step=1,
                args={"q": "saxon"},
                display_label="Searching library",
            ))
            logger.handle(ToolCompleted(
                request_id="rq-c01-0001",
                emitted_at_ms=30,
                tool_call_id="A",
                tool_name="roon_search",
                step=1,
                result=None,
                duration_ms=10,
            ))
            logger.handle(RequestCompleted(
                request_id="rq-c01-0001",
                emitted_at_ms=40,
                status="completed",
                chat_response="Done.",
                total_duration_ms=40,
                total_steps=2,
                usage=None,
                coordinator_model="m",
            ))

            events_path = logger.request_dir / "events.jsonl"
            lines = events_path.read_text().strip().split("\n")
            event_types = [json.loads(line)["event_type"] for line in lines]
            self.assertEqual(event_types, [
                "RequestStarted",
                "LlmCallStarted",
                "ToolStarted",
                "ToolCompleted",
                "RequestCompleted",
            ])

    def test_null_request_logger_handle_is_silent_noop(self) -> None:
        from app.runtime.request_logger import NullRequestLogger
        logger = NullRequestLogger()
        # Should not raise — NullRequestLogger ignores bus events.
        logger.handle(RequestStarted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            user_input="hi",
            coordinator_model=None,
            run_mode_label="cli",
        ))


if __name__ == "__main__":
    unittest.main()
