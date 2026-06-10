"""Contract tests for the AgentEvent stream emitted by ``process_request``.

These tests pin down which events fire, in what order, with what
fields, for representative request shapes. They survive refactors that
preserve the agent's observable lifecycle.

The event stream is the contract every transport subscribes to —
CLI renderer, WS broadcaster, RequestLogger, and any future adapter.
If a refactor changes one of these sequences without an explicit
behavioral reason, this is the test that catches it.
"""

from __future__ import annotations

import os
import unittest
from typing import List
from unittest.mock import MagicMock, patch

from app.coordinator.event_bus import EventBus
from app.coordinator.events import (
    AgentEvent,
    ChatResponseEmitted,
    LlmCallCompleted,
    LlmCallStarted,
    RequestCompleted,
    RequestStarted,
    ToolCompleted,
    ToolStarted,
)
from app.llm.client import LLMResponse, ToolCall


def _make_runtime():
    """Minimal initialised RuntimeState for event-stream tests.

    Real tool registry on the call path; Roon + skill-loading patched
    so tests don't touch them. Diagnostic agent disabled so the event
    sequence is deterministic.
    """
    from app.runtime.state import RuntimeState

    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
        "ENABLE_DIAGNOSTIC_AGENT": "false",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", MagicMock),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("", ""),
        ),
    ):
        rs = RuntimeState()
        rs.ensure_initialised()
    rs.roon_connection.session_manager.new_search_session.return_value = "test-session"
    return rs


class _ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0
        self.model = "dummy/dummy-model"

    async def completion(self, messages, tools=None):
        idx = min(self._i, len(self._responses) - 1)
        self._i += 1
        return self._responses[idx]


def _run_request(runtime, user_input, events_out: List[AgentEvent]) -> None:
    from app.coordinator.request_flow import process_request
    bus = EventBus()
    bus.subscribe(events_out.append)
    process_request(
        runtime=runtime,
        user_input=user_input,
        cancel_event=None,
        event_bus=bus,
    )


class TestEventBusContract(unittest.TestCase):
    """Bus mechanics independent of any production emission."""

    def test_subscribers_invoked_in_subscribe_order(self) -> None:
        bus = EventBus()
        order: List[str] = []
        bus.subscribe(lambda _e: order.append("first"))
        bus.subscribe(lambda _e: order.append("second"))
        bus.subscribe(lambda _e: order.append("third"))

        bus.emit(RequestStarted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            user_input="hi",
            coordinator_model="dummy/dummy-model",
            run_mode_label="cli",
        ))

        self.assertEqual(order, ["first", "second", "third"])

    def test_subscriber_exception_does_not_break_other_subscribers(self) -> None:
        bus = EventBus()
        received: List[AgentEvent] = []

        def _raises(_e):
            raise RuntimeError("subscriber misbehaved")

        bus.subscribe(_raises)
        bus.subscribe(received.append)
        bus.subscribe(_raises)

        event = RequestStarted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            user_input="hi",
            coordinator_model=None,
            run_mode_label="cli",
        )
        bus.emit(event)

        self.assertEqual(received, [event])

    def test_no_subscribers_is_silent_noop(self) -> None:
        EventBus().emit(RequestStarted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            user_input="hi",
            coordinator_model=None,
            run_mode_label="cli",
        ))


class TestEventStreamLifecycle(unittest.TestCase):
    """Pin the event sequence emitted by representative request shapes."""

    def test_text_only_response_emits_single_llm_cycle(self) -> None:
        """No tool calls — one LlmCallStarted + LlmCallCompleted
        (terminal), plus the chat response and request markers."""
        runtime = _make_runtime()
        runtime.llm_client = _ScriptedClient([
            LLMResponse(
                text="Hello.",
                tool_calls=[],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
        ])

        events: List[AgentEvent] = []
        _run_request(runtime, "hi", events)

        types = [type(e).__name__ for e in events]
        self.assertEqual(types, [
            "RequestStarted",
            "LlmCallStarted",
            "LlmCallCompleted",
            "ChatResponseEmitted",
            "TtsSpeakRequested",
            "RequestCompleted",
        ])

        [start] = [e for e in events if isinstance(e, RequestStarted)]
        self.assertEqual(start.user_input, "hi")
        self.assertEqual(start.run_mode_label, "cli")

        [llm_complete] = [e for e in events if isinstance(e, LlmCallCompleted)]
        self.assertFalse(llm_complete.has_tool_calls)
        self.assertTrue(llm_complete.has_text)
        self.assertTrue(llm_complete.is_terminal)
        self.assertEqual(llm_complete.selected_tools, ())

        [chat] = [e for e in events if isinstance(e, ChatResponseEmitted)]
        self.assertEqual(chat.text, "Hello.")

        [done] = [e for e in events if isinstance(e, RequestCompleted)]
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.chat_response, "Hello.")
        self.assertEqual(done.total_steps, 1)

    def test_single_tool_request_emits_tool_lifecycle_between_llm_calls(self) -> None:
        """Tool call → text. Sequence:
            LlmCallStarted (1) → LlmCallCompleted (1, tool calls) →
            ToolStarted → ToolCompleted →
            LlmCallStarted (2) → LlmCallCompleted (2, terminal) →
            ChatResponseEmitted → RequestCompleted

        The second LlmCallStarted is the "Thinking" signal that lets the
        UI flip off the tool label as soon as the wrap-up LLM call
        begins."""
        runtime = _make_runtime()
        runtime.llm_client = _ScriptedClient([
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(
                    id="call_1",
                    name="roon_status",
                    arguments={"zone_name": "Living Room", "what": "status"},
                )],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
            LLMResponse(
                text="Done.",
                tool_calls=[],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
        ])

        events: List[AgentEvent] = []
        _run_request(runtime, "what's playing", events)

        types = [type(e).__name__ for e in events]
        self.assertEqual(types, [
            "RequestStarted",
            "LlmCallStarted",
            "LlmCallCompleted",
            "ToolStarted",
            "ToolCompleted",
            "LlmCallStarted",
            "LlmCallCompleted",
            "ChatResponseEmitted",
            "TtsSpeakRequested",
            "RequestCompleted",
        ])

        [tool_started] = [e for e in events if isinstance(e, ToolStarted)]
        self.assertEqual(tool_started.tool_name, "roon_status")
        self.assertEqual(tool_started.tool_call_id, "call_1")
        # roon_status registers its display_label as "Checking zone status".
        self.assertEqual(tool_started.display_label, "Checking zone status")
        self.assertEqual(tool_started.step, 1)

        [tool_completed] = [e for e in events if isinstance(e, ToolCompleted)]
        self.assertEqual(tool_completed.tool_call_id, "call_1")
        self.assertEqual(tool_completed.tool_name, "roon_status")

        llm_starts = [e for e in events if isinstance(e, LlmCallStarted)]
        self.assertEqual(len(llm_starts), 2)
        self.assertEqual(llm_starts[0].step, 1)
        self.assertEqual(llm_starts[1].step, 2)

        llm_completes = [e for e in events if isinstance(e, LlmCallCompleted)]
        self.assertEqual(len(llm_completes), 2)
        self.assertTrue(llm_completes[0].has_tool_calls)
        self.assertFalse(llm_completes[0].is_terminal)
        self.assertEqual(llm_completes[0].selected_tools, ("roon_status",))
        self.assertFalse(llm_completes[1].has_tool_calls)
        self.assertTrue(llm_completes[1].is_terminal)

    def test_two_step_tool_request_emits_two_tool_cycles(self) -> None:
        """Search → action → text. Each LLM call followed by exactly
        one tool, then a final terminal LLM call. This is the c16
        shape — proves the wrap-up LlmCallStarted fires, which is the
        whole point of the activity-indicator fix."""
        runtime = _make_runtime()
        runtime.llm_client = _ScriptedClient([
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(
                    id="call_search",
                    name="roon_search",
                    arguments={"operation": "new_search", "search_string": "Saxon"},
                )],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(
                    id="call_action",
                    name="roon_action",
                    arguments={
                        "action": "Play Now",
                        "intended_item_category": "track",
                        "items": [{"title": "Wheels of Steel", "reference": "S:abc"}],
                    },
                )],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
            LLMResponse(
                text="Playing now.",
                tool_calls=[],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
        ])

        events: List[AgentEvent] = []
        _run_request(runtime, "play Saxon", events)

        types = [type(e).__name__ for e in events]
        self.assertEqual(types, [
            "RequestStarted",
            "LlmCallStarted",
            "LlmCallCompleted",
            "ToolStarted",
            "ToolCompleted",
            "LlmCallStarted",
            "LlmCallCompleted",
            "ToolStarted",
            "ToolCompleted",
            "LlmCallStarted",
            "LlmCallCompleted",
            "ChatResponseEmitted",
            "TtsSpeakRequested",
            "RequestCompleted",
        ])

        tool_names = [e.tool_name for e in events if isinstance(e, ToolStarted)]
        self.assertEqual(tool_names, ["roon_search", "roon_action"])

        # Without the final LlmCallStarted, the UI has no signal to flip
        # off the previous tool label.
        llm_starts = [e for e in events if isinstance(e, LlmCallStarted)]
        self.assertEqual([s.step for s in llm_starts], [1, 2, 3])

        # Only the final LLM call is terminal.
        llm_completes = [e for e in events if isinstance(e, LlmCallCompleted)]
        self.assertEqual([c.is_terminal for c in llm_completes], [False, False, True])


if __name__ == "__main__":
    unittest.main()
