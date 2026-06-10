"""Tests for conversation-aware logging: outcome assignment fields and conversation_summary.json."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from app.runtime.request_logger import NullRequestLogger, RequestLogger


class TestOutcomeAssignmentFields(unittest.TestCase):
    """log_outcome() should include conversation assignment fields when provided."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_logger(self, request_id="rq-c01-0001"):
        return RequestLogger(request_id, logs_root=self.tmp)

    def test_outcome_includes_assignment_fields(self):
        logger = self._make_logger()
        logger.log_outcome(
            status="completed",
            chat_response="Done",
            total_steps=2,
            topic_summary="Playing jazz music",
            assignment_source="diagnostic_agent",
        )
        outcome = json.loads((logger.request_dir / "outcome.json").read_text())
        assert outcome["topic_summary"] == "Playing jazz music"
        assert outcome["assignment_source"] == "diagnostic_agent"

    def test_outcome_omits_assignment_when_none(self):
        logger = self._make_logger()
        logger.log_outcome(status="completed", total_steps=1)
        outcome = json.loads((logger.request_dir / "outcome.json").read_text())
        assert "topic_summary" not in outcome
        assert "assignment_source" not in outcome


class TestConversationSummary(unittest.TestCase):
    """update_conversation_summary() manages conversation_summary.json in the cXX dir."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_logger(self, request_id="rq-c01-0001"):
        return RequestLogger(request_id, logs_root=self.tmp)

    def _read_summary(self, logger):
        summary_path = logger.request_dir.parent / "conversation_summary.json"
        return json.loads(summary_path.read_text())

    def test_conversation_summary_creates_new(self):
        logger = self._make_logger()
        logger.update_conversation_summary(topic_summary="Playing jazz music")
        summary = self._read_summary(logger)
        assert summary["conversation_id"] == "c01"
        assert summary["topic_summary"] == "Playing jazz music"
        assert summary["requests"] == ["rq-c01-0001"]
        assert "updated_at" in summary

    def test_conversation_summary_appends_request(self):
        logger1 = self._make_logger("rq-c01-0001")
        logger1.update_conversation_summary(topic_summary="Playing jazz")
        logger2 = self._make_logger("rq-c01-0002")
        logger2.update_conversation_summary(topic_summary="Playing jazz — updated")
        summary = self._read_summary(logger2)
        assert summary["requests"] == ["rq-c01-0001", "rq-c01-0002"]
        assert summary["topic_summary"] == "Playing jazz — updated"

    def test_conversation_summary_deduplicates(self):
        logger = self._make_logger("rq-c01-0001")
        logger.update_conversation_summary(topic_summary="Jazz")
        logger.update_conversation_summary(topic_summary="Jazz")
        summary = self._read_summary(logger)
        assert summary["requests"] == ["rq-c01-0001"]

    def test_conversation_summary_updates_topic(self):
        logger = self._make_logger("rq-c01-0001")
        logger.update_conversation_summary(topic_summary="Jazz")
        summary = self._read_summary(logger)
        assert summary["topic_summary"] == "Jazz"
        logger.update_conversation_summary(topic_summary="Jazz and blues")
        summary = self._read_summary(logger)
        assert summary["topic_summary"] == "Jazz and blues"

    def test_conversation_summary_records_request_even_with_no_topic(self):
        """``topic_summary=None`` still appends the request_id to the
        requests list with a null topic in the file."""
        logger = self._make_logger("rq-c01-0001")
        logger.update_conversation_summary(topic_summary=None)
        summary = self._read_summary(logger)
        assert summary["requests"] == ["rq-c01-0001"]
        assert summary["topic_summary"] is None

    def test_conversation_summary_preserves_existing_topic_when_none_passed(self):
        """``topic_summary=None`` preserves any existing topic in the file."""
        logger1 = self._make_logger("rq-c01-0001")
        logger1.update_conversation_summary(topic_summary="Jazz")
        logger2 = self._make_logger("rq-c01-0002")
        logger2.update_conversation_summary(topic_summary=None)
        summary = self._read_summary(logger2)
        assert summary["topic_summary"] == "Jazz"
        assert summary["requests"] == ["rq-c01-0001", "rq-c01-0002"]


class TestInterruptedRequestUpdatesSummary(unittest.TestCase):
    """``process_request`` calls ``update_conversation_summary`` on
    every terminal path (completed, interrupted, errored)."""

    def test_request_interrupted_in_loop_updates_conversation_summary(self):
        try:
            from tests._runtime_fixtures import make_request_runtime
        except ModuleNotFoundError:
            from _runtime_fixtures import make_request_runtime  # type: ignore[no-redef]
        from app.coordinator.event_bus import EventBus
        from app.coordinator.request_flow import process_request
        from app.exceptions import RequestInterrupted

        runtime = make_request_runtime()

        async def _interrupting(messages, tools=None):
            raise RequestInterrupted("test interrupt")

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _interrupting

        logger = MagicMock()
        logger.request_id = "rq-c99-0001"
        logger.handle = MagicMock()

        process_request(
            runtime=runtime,
            user_input="play something",
            cancel_event=None,
            event_bus=EventBus(),
            request_logger=logger,
            run_mode_label="ws",
        )

        self.assertTrue(
            logger.update_conversation_summary.called,
            "Interrupted run must still call update_conversation_summary "
            "so the request_id lands in the conversation's request list.",
        )

    def test_request_with_loop_exception_updates_conversation_summary(self):
        try:
            from tests._runtime_fixtures import make_request_runtime
        except ModuleNotFoundError:
            from _runtime_fixtures import make_request_runtime  # type: ignore[no-redef]
        from app.coordinator.event_bus import EventBus
        from app.coordinator.request_flow import process_request

        runtime = make_request_runtime()

        async def _boom(messages, tools=None):
            raise RuntimeError("provider blew up")

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _boom

        logger = MagicMock()
        logger.request_id = "rq-c99-0002"
        logger.handle = MagicMock()

        process_request(
            runtime=runtime,
            user_input="play something",
            cancel_event=None,
            event_bus=EventBus(),
            request_logger=logger,
            run_mode_label="ws",
        )

        self.assertTrue(
            logger.update_conversation_summary.called,
            "Failed run must still call update_conversation_summary.",
        )


class TestNullLoggerStubs(unittest.TestCase):
    """NullRequestLogger should accept the new methods without acting on them."""

    def test_null_logger_log_outcome_accepts_new_params(self):
        logger = NullRequestLogger()
        # Should not raise
        logger.log_outcome(
            status="completed",
            topic_summary="test",
            assignment_source="diagnostic_agent",
        )

    def test_null_logger_update_conversation_summary(self):
        logger = NullRequestLogger()
        # Should not raise
        logger.update_conversation_summary(topic_summary="test")
