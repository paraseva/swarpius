"""Contract tests for ``CliRenderer``'s multi-row spinner state.

The renderer owns the in-flight display: it tracks active tool rows
keyed by ``tool_call_id`` plus a "Thinking..." row when the
coordinator is between tools. ``active_row_labels()`` reports the
labels that would be drawn into the Live(Group) — tests assert on
this instead of poking the Rich Live internals.

These tests don't start the Live display; they exercise the
state-mutation half of the renderer in isolation.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.cli.renderer import CliRenderer
from app.coordinator.events import (
    ChatResponseEmitted,
    DiagnosticClassificationCompleted,
    DiagnosticClassificationStarted,
    LlmCallStarted,
    RequestCompleted,
    RequestFailed,
    RequestStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
)


def _make_renderer() -> CliRenderer:
    return CliRenderer(rich_console=MagicMock())


def _llm_call_started(step: int = 1) -> LlmCallStarted:
    return LlmCallStarted(
        request_id="rq-c01-0001",
        emitted_at_ms=0,
        call_id=f"rq-c01-0001-step{step}",
        step=step,
        agent_name="Coordinator",
        model="dummy/dummy-model",
        prompt_tokens_estimated=0,
        prompt_diagnostics={},
    )


def _tool_started(tool_call_id: str, tool_name: str, display_label: str, step: int = 1) -> ToolStarted:
    return ToolStarted(
        request_id="rq-c01-0001",
        emitted_at_ms=0,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        step=step,
        args={},
        display_label=display_label,
    )


def _tool_completed(tool_call_id: str, tool_name: str, step: int = 1) -> ToolCompleted:
    return ToolCompleted(
        request_id="rq-c01-0001",
        emitted_at_ms=0,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        step=step,
        result=None,
        duration_ms=10,
    )


def _diagnostic_started() -> DiagnosticClassificationStarted:
    # request_id is None: the classification runs before the ID is
    # minted (the classification decides the cXX in the eventual ID).
    return DiagnosticClassificationStarted(
        request_id=None,
        emitted_at_ms=0,
        call_id="diag-0001",
        agent_name="Diagnostic",
        model="dummy/dummy-model",
    )


def _diagnostic_completed(success: bool = True) -> DiagnosticClassificationCompleted:
    return DiagnosticClassificationCompleted(
        request_id="rq-c01-0001",
        emitted_at_ms=0,
        call_id="diag-0001",
        conversation_id="c01",
        topic_summary="test topic",
        is_new=True,
        success=success,
    )


class TestDiagnosticClassificationFlow(unittest.TestCase):
    def test_diagnostic_started_shows_classifying_row(self) -> None:
        r = _make_renderer()
        r.handle(_diagnostic_started())
        self.assertEqual(r.active_row_labels(), ["Classifying"])

    def test_diagnostic_completed_clears_classifying_row(self) -> None:
        r = _make_renderer()
        r.handle(_diagnostic_started())
        # Precondition — if started never shows the row, the cleared-row
        # assertion below is a tautology.
        self.assertEqual(r.active_row_labels(), ["Classifying"])
        r.handle(_diagnostic_completed())
        self.assertEqual(r.active_row_labels(), [])

    def test_thinking_replaces_classifying_after_completion(self) -> None:
        r = _make_renderer()
        r.handle(_diagnostic_started())
        self.assertEqual(r.active_row_labels(), ["Classifying"])
        r.handle(_diagnostic_completed())
        r.handle(_llm_call_started())
        self.assertEqual(r.active_row_labels(), ["Thinking"])

    def test_failed_classification_still_clears_the_row(self) -> None:
        r = _make_renderer()
        r.handle(_diagnostic_started())
        self.assertEqual(r.active_row_labels(), ["Classifying"])
        r.handle(_diagnostic_completed(success=False))
        self.assertEqual(r.active_row_labels(), [])


class TestSequentialToolFlow(unittest.TestCase):
    def test_llm_call_started_shows_thinking_row(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        self.assertEqual(r.active_row_labels(), ["Thinking"])

    def test_tool_started_replaces_thinking_with_tool_row(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "roon_search", "Searching library"))
        self.assertEqual(r.active_row_labels(), ["Searching library"])

    def test_tool_completed_returns_to_thinking_row(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "roon_search", "Searching library"))
        r.handle(_tool_completed("A", "roon_search"))
        self.assertEqual(r.active_row_labels(), ["Thinking"])

    def test_tool_failed_returns_to_thinking_row(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "roon_search", "Searching library"))
        r.handle(ToolFailed(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            tool_call_id="A",
            tool_name="roon_search",
            step=1,
            error="boom",
            duration_ms=5,
        ))
        self.assertEqual(r.active_row_labels(), ["Thinking"])

    def test_chat_response_clears_active_rows(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(ChatResponseEmitted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            text="Hello.",
            agent_name="Coordinator",
        ))
        self.assertEqual(r.active_row_labels(), [])


class TestParallelToolFlow(unittest.TestCase):
    def test_two_parallel_tools_show_two_rows(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "web_search", "Searching the web"))
        r.handle(_tool_started("B", "roon_search", "Searching library"))
        self.assertEqual(
            r.active_row_labels(),
            ["Searching the web", "Searching library"],
        )

    def test_one_parallel_tool_completing_leaves_the_other(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "web_search", "Searching the web"))
        r.handle(_tool_started("B", "roon_search", "Searching library"))
        r.handle(_tool_completed("A", "web_search"))
        self.assertEqual(r.active_row_labels(), ["Searching library"])

    def test_all_parallel_tools_completed_returns_to_thinking(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "web_search", "Searching the web"))
        r.handle(_tool_started("B", "roon_search", "Searching library"))
        r.handle(_tool_completed("A", "web_search"))
        r.handle(_tool_completed("B", "roon_search"))
        self.assertEqual(r.active_row_labels(), ["Thinking"])

    def test_parallel_rows_render_in_dispatch_order(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("first", "a", "First"))
        r.handle(_tool_started("second", "b", "Second"))
        r.handle(_tool_started("third", "c", "Third"))
        self.assertEqual(r.active_row_labels(), ["First", "Second", "Third"])

    def test_unknown_tool_call_id_completion_is_idempotent(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "x", "A"))
        r.handle(_tool_completed("ghost", "y"))
        # Row A is still active; ghost completion didn't affect it.
        self.assertEqual(r.active_row_labels(), ["A"])


class TestLifecycleClear(unittest.TestCase):
    def test_request_completed_clears_rows(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "x", "Working"))
        r.handle(RequestCompleted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            status="completed",
            chat_response="Done.",
            total_duration_ms=100,
            total_steps=1,
            usage=None,
            coordinator_model="dummy/dummy-model",
        ))
        self.assertEqual(r.active_row_labels(), [])

    def test_request_failed_clears_rows(self) -> None:
        r = _make_renderer()
        r.handle(_llm_call_started())
        r.handle(_tool_started("A", "x", "Working"))
        r.handle(RequestFailed(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            error="provider down",
            summary="provider down",
            coordinator_model="dummy/dummy-model",
            is_rate_limited=False,
        ))
        self.assertEqual(r.active_row_labels(), [])


class TestRequestStartedPanel(unittest.TestCase):
    def test_request_started_prints_user_input_panel(self) -> None:
        console = MagicMock()
        r = CliRenderer(rich_console=console)
        r.handle(RequestStarted(
            request_id="rq-c01-0001",
            emitted_at_ms=0,
            user_input="hello",
            coordinator_model="dummy/dummy-model",
            run_mode_label="cli",
        ))
        # Panel printed exactly once via console.print.
        self.assertEqual(console.print.call_count, 1)


if __name__ == "__main__":
    unittest.main()
