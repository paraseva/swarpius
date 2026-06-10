"""Tests for _run_diagnostic_classification: the helper extracted from
process_request that handles the optional diagnostic-agent step.

Pins the contract: when enabled + client available, run classification
with a 5s timeout; when disabled or unavailable, skip cleanly; emit
DiagnosticClassificationStarted / Completed on the bus; never let a
diagnostic-agent failure break the request.

Stubs only at the LiteLLM boundary (``runtime.diagnostic_client.completion``);
the real DiagnosticAgent runs end-to-end so prompt building, response
parsing, and tracker integration are all on the call path.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.coordinator.event_bus import EventBus
from app.coordinator.events import (
    DiagnosticClassificationCompleted,
    DiagnosticClassificationStarted,
)
from app.coordinator.request_flow import _run_diagnostic_classification
from app.llm.client import LLMResponse
from app.runtime.conversation_tracker import ConversationTracker

_enabled_env = patch.dict("os.environ", {"ENABLE_DIAGNOSTIC_AGENT": "true"})
_disabled_env = patch.dict("os.environ", {"ENABLE_DIAGNOSTIC_AGENT": "false"})


def _make_gen(tracker=None):
    """Real ConversationTracker on a RequestIdGenerator stand-in."""
    return SimpleNamespace(tracker=tracker or ConversationTracker())


def _make_client(text: str | None = None, raises: Exception | None = None):
    """diagnostic_client double — stubs only the litellm boundary."""
    client = AsyncMock()
    client.model = "anthropic/claude-haiku"
    if raises is not None:
        client.completion = AsyncMock(side_effect=raises)
    else:
        client.completion = AsyncMock(return_value=LLMResponse(text=text or ""))
    return client


def _make_runtime(diagnostic_client):
    return SimpleNamespace(diagnostic_client=diagnostic_client)


def _spy_bus():
    """Return (events_list, bus) where events_list captures everything
    emitted on the bus."""
    events: list = []
    bus = EventBus()
    bus.subscribe(events.append)
    return events, bus


class TestShortCircuits(unittest.TestCase):
    """Returns None without emitting any events when classification is off."""

    def test_returns_none_when_diagnostic_client_is_none(self):
        runtime = _make_runtime(None)
        events, bus = _spy_bus()

        result = _run_diagnostic_classification(
            gen=_make_gen(),
            runtime=runtime,
            user_input="play jazz",
            bus=bus,
        )

        self.assertIsNone(result)
        self.assertEqual(events, [])

    @_disabled_env
    def test_returns_none_when_feature_flag_disabled(self):
        runtime = _make_runtime(_make_client('{"is_new": true, "topic_summary": "x"}'))
        events, bus = _spy_bus()

        result = _run_diagnostic_classification(
            gen=_make_gen(),
            runtime=runtime,
            user_input="play jazz",
            bus=bus,
        )

        self.assertIsNone(result)
        self.assertEqual(events, [])


@_enabled_env
class TestClassificationSuccess(unittest.TestCase):

    def test_returns_assignment_and_emits_bus_events(self):
        """On enabled + successful classification: returns the
        assignment and emits DiagnosticClassificationStarted +
        DiagnosticClassificationCompleted on the bus, paired by
        call_id."""
        client = _make_client(
            '{"is_new": true, "topic_summary": "jazz"}',
        )
        runtime = _make_runtime(client)
        events, bus = _spy_bus()

        result = _run_diagnostic_classification(
            gen=_make_gen(),
            runtime=runtime,
            user_input="play jazz",
            bus=bus,
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.is_new)
        self.assertEqual(result.topic_summary, "jazz")
        client.completion.assert_awaited_once()

        started = [e for e in events if isinstance(e, DiagnosticClassificationStarted)]
        completed = [e for e in events if isinstance(e, DiagnosticClassificationCompleted)]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(started[0].agent_name, "Diagnostic")
        self.assertEqual(started[0].call_id, completed[0].call_id)
        self.assertTrue(completed[0].success)
        self.assertEqual(completed[0].topic_summary, "jazz")
        self.assertTrue(completed[0].is_new)

    def test_existing_conversation_assignment_parsed(self):
        """When the LLM returns an existing conversation_id, the real
        parser surfaces it (covers the parse path, not just the new-conv
        path)."""
        tracker = ConversationTracker()
        tracker.assign_by_timeout()  # mints c01
        client = _make_client(
            '{"conversation_id": "c01", "is_new": false, "topic_summary": "still jazz"}',
        )
        runtime = _make_runtime(client)
        events, bus = _spy_bus()

        result = _run_diagnostic_classification(
            gen=_make_gen(tracker),
            runtime=runtime,
            user_input="more jazz",
            bus=bus,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.conversation_id, "c01")
        self.assertFalse(result.is_new)

        completed = [e for e in events if isinstance(e, DiagnosticClassificationCompleted)]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].conversation_id, "c01")

    def test_bus_events_fire_regardless_of_transport(self):
        """The bus contract: classification always emits its events.
        Transport adapters (WsBroadcaster vs CliRenderer) decide what
        to render. The helper itself doesn't gate on run_mode."""
        client = _make_client(
            '{"is_new": false, "conversation_id": "c01", "topic_summary": "jazz"}',
        )
        tracker = ConversationTracker()
        tracker.assign_by_timeout()
        runtime = _make_runtime(client)
        events, bus = _spy_bus()

        result = _run_diagnostic_classification(
            gen=_make_gen(tracker),
            runtime=runtime,
            user_input="play jazz",
            bus=bus,
        )

        self.assertIsNotNone(result)
        started = [e for e in events if isinstance(e, DiagnosticClassificationStarted)]
        completed = [e for e in events if isinstance(e, DiagnosticClassificationCompleted)]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed), 1)


@_enabled_env
class TestClassificationFailure(unittest.TestCase):

    def test_agent_raises_returns_none_but_still_emits_completed(self):
        """If the LLM call blows up, the helper swallows the exception
        and returns None — the request carries on via the idle-timeout
        fallback. Completed event still fires (with success=False) so
        adapters can clear any spinner they showed for Started."""
        client = _make_client(raises=RuntimeError("boom"))
        runtime = _make_runtime(client)
        events, bus = _spy_bus()

        result = _run_diagnostic_classification(
            gen=_make_gen(),
            runtime=runtime,
            user_input="play jazz",
            bus=bus,
        )

        self.assertIsNone(result)
        completed = [e for e in events if isinstance(e, DiagnosticClassificationCompleted)]
        self.assertEqual(len(completed), 1)
        self.assertFalse(completed[0].success)

    def test_agent_failure_is_logged(self):
        """A diagnostic-agent LLM failure must reach the server log so
        we can diagnose silent classification fallbacks instead of
        speculating about them."""
        client = _make_client(raises=RuntimeError("provider down"))
        runtime = _make_runtime(client)
        _events, bus = _spy_bus()

        with _enabled_env, self.assertLogs("swarpius.request_flow", level="WARNING") as cm:
            _run_diagnostic_classification(
                gen=_make_gen(), runtime=runtime, user_input="play jazz", bus=bus,
            )
        self.assertTrue(any("Diagnostic agent (classification)" in line for line in cm.output))

    def test_successful_classification_is_logged(self):
        """A successful classification logs at INFO so the conversation
        assignment is visible in swarpius.log, not just the WS stream."""
        client = _make_client(text='{"is_new": true, "topic_summary": "Jazz playback"}')
        runtime = _make_runtime(client)
        _events, bus = _spy_bus()

        with _enabled_env, self.assertLogs("swarpius.request_flow", level="INFO") as cm:
            _run_diagnostic_classification(
                gen=_make_gen(), runtime=runtime, user_input="play jazz", bus=bus,
            )
        self.assertTrue(any("Diagnostic classification" in line for line in cm.output))

    def test_broadcaster_emits_call_failed_when_success_is_false(self):
        """The diagnostic-agent transport contract: a Completed event
        with success=False must surface as ``call_failed`` on the
        llm-diagnostics channel (not ``call_completed``) and carry the
        error string."""
        from unittest.mock import MagicMock as _MagicMock

        from app.io.ws_broadcaster import WsBroadcaster

        sent: list[tuple[str, dict]] = []

        def _send(ch, p, meta=None):  # noqa: ARG001 — meta unused but required
            sent.append((ch, p))

        broadcaster = WsBroadcaster(
            ws_send_fn=_send,
            runtime=_MagicMock(),
        )

        event = DiagnosticClassificationCompleted(
            request_id=None,
            emitted_at_ms=0,
            call_id="diag-x",
            conversation_id=None,
            topic_summary=None,
            is_new=None,
            success=False,
            error="kaboom",
        )
        broadcaster.handle(event)

        diag = [p for (ch, p) in sent if ch == "llm-diagnostics"]
        types = [p.get("event_type") for p in diag]
        self.assertIn("call_failed", types)
        self.assertNotIn("call_completed", types)
        failed = next(p for p in diag if p["event_type"] == "call_failed")
        self.assertEqual(failed.get("error"), "kaboom")

    def test_agent_raises_carries_error_text_on_completed_event(self):
        """The Completed event must carry the exception text so the
        ws_broadcaster can surface it as ``call_failed.error`` on the
        llm-diagnostics channel."""
        client = _make_client(raises=RuntimeError("kaboom — server gone"))
        runtime = _make_runtime(client)
        events, bus = _spy_bus()

        _run_diagnostic_classification(
            gen=_make_gen(),
            runtime=runtime,
            user_input="play jazz",
            bus=bus,
        )

        completed = next(
            e for e in events if isinstance(e, DiagnosticClassificationCompleted)
        )
        self.assertIsNotNone(completed.error)
        self.assertIn("kaboom", completed.error or "")


if __name__ == "__main__":
    unittest.main()
