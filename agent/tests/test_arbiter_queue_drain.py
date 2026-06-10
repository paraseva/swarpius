"""Tests for the WS chat-message queue drain after the arbiter decides.

Contract: after the arbiter's decision is applied to ``state``, the
session must drain any pending messages — not wait for a future
trigger. The active task's ``_runner.finally`` would otherwise be
the only drain path, which races against the arbiter LLM call: if
the active task completes while the arbiter is in flight, the
``finally`` runs with an empty queue, the arbiter then appends to
the queue, and the message stays stuck until the next incoming chat
message reopens the drain path.
"""

from __future__ import annotations

import threading
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.websocket_flow import (  # noqa: E402
    ChatMessage,
    WebsocketSessionState,
    _apply_arbiter_decision,
)
from app.schemas import InterruptArbiterOutputSchema  # noqa: E402


def _decision(action: str) -> InterruptArbiterOutputSchema:
    return InterruptArbiterOutputSchema(
        action=action, reason="test", confidence=0.5,
    )


def _msg(body: str, client_msg_id: str | None = None) -> ChatMessage:
    return ChatMessage(body=body, client_msg_id=client_msg_id)


class TestApplyArbiterDecisionDrains(unittest.IsolatedAsyncioTestCase):
    async def _make_drain(self, state: WebsocketSessionState):
        """Drain stand-in that pops one pending message per call when
        ``active_task`` is None — mirrors the production
        ``_start_next_if_idle`` shape."""
        processed: list[ChatMessage] = []

        async def fake_drain() -> None:
            if state.active_task is not None:
                return
            if not state.pending_messages:
                return
            processed.append(state.pending_messages.popleft())

        return fake_drain, processed

    async def test_queue_decision_drains_when_active_already_finished(self):
        """The race: active task finished during the arbiter call,
        so by the time we apply the queue decision, ``active_task``
        is None and no future ``_runner.finally`` will fire. The
        decision-application step must call drain itself."""
        state = WebsocketSessionState()
        state.active_task = None
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("hello"),
            decision=_decision("queue"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(
            [m.body for m in processed], ["hello"],
            "Queued message must be processed when active_task is None.",
        )
        self.assertEqual(len(state.pending_messages), 0)

    async def test_queue_decision_leaves_drain_to_runner_when_active_still_running(self):
        """When the active task is still in flight, the decision
        appends but the drain returns early (active_task != None). The
        eventual ``_runner.finally`` is responsible for picking it up
        — this test pins that the decision-application step is safe
        to call regardless of active state."""
        state = WebsocketSessionState()
        state.active_task = object()
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("hello"),
            decision=_decision("queue"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(processed, [])
        self.assertEqual(len(state.pending_messages), 1)

    async def test_interrupt_and_replace_drains_when_active_already_finished(self):
        """Same race as the queue case: if the active task finished
        during the arbiter call, ``appendleft`` alone leaves the new
        message stuck. The decision-application step must drain."""
        state = WebsocketSessionState()
        state.active_task = None
        state.active_cancel_event = None
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("replace"),
            decision=_decision("interrupt_and_replace"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual([m.body for m in processed], ["replace"])

    async def test_interrupt_only_does_not_queue_anything(self):
        """``interrupt_only`` cancels the active task and discards
        the new message — drain must not fire for it."""
        state = WebsocketSessionState()
        state.active_task = object()
        state.active_cancel_event = threading.Event()
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("stop"),
            decision=_decision("interrupt_only"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(processed, [])
        self.assertEqual(len(state.pending_messages), 0)

    async def test_interrupt_only_emits_directive_ack(self):
        """``interrupt_only`` must emit ``control_command_acknowledged``
        carrying the message's ``client_msg_id`` so the FE renders it
        as a Directive pill — identical to a keyword 'stop'/'cancel',
        not a badge-less chat bubble that looks like a dropped request."""
        from app.constants import CHANNEL_AGENT_OUTPUTS
        state = WebsocketSessionState()
        state.active_task = object()
        state.active_cancel_event = threading.Event()
        fake_drain, _ = await self._make_drain(state)
        sent: list[tuple[str, dict]] = []

        await _apply_arbiter_decision(
            state, message=_msg("Actually no forget that", client_msg_id="fe-uuid-9"),
            decision=_decision("interrupt_only"),
            start_next_if_idle=fake_drain,
            ws_send_fn=lambda ch, p: sent.append((ch, p)),
        )

        acks = [
            (ch, p) for (ch, p) in sent
            if p.get("event_type") == "control_command_acknowledged"
        ]
        self.assertEqual(len(acks), 1)
        ch, payload = acks[0]
        self.assertEqual(ch, CHANNEL_AGENT_OUTPUTS)
        self.assertEqual(payload["action"], "interrupt_only")
        self.assertEqual(payload["client_msg_id"], "fe-uuid-9")

    async def test_non_interrupt_only_does_not_emit_directive_ack(self):
        """queue / interrupt_and_replace become real requests, not cancel
        directives — they must NOT be acknowledged as control commands."""
        state = WebsocketSessionState()
        state.active_task = None
        fake_drain, _ = await self._make_drain(state)
        sent: list[tuple[str, dict]] = []

        await _apply_arbiter_decision(
            state, message=_msg("play something", client_msg_id="fe-uuid-x"),
            decision=_decision("queue"),
            start_next_if_idle=fake_drain,
            ws_send_fn=lambda ch, p: sent.append((ch, p)),
        )

        acks = [
            p for (ch, p) in sent
            if p.get("event_type") == "control_command_acknowledged"
        ]
        self.assertEqual(acks, [])

    async def test_queue_preserves_client_msg_id_through_drain(self):
        """A queued message keeps its ``client_msg_id`` on the way out."""
        state = WebsocketSessionState()
        state.active_task = None
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("hello", client_msg_id="fe-uuid-1"),
            decision=_decision("queue"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].body, "hello")
        self.assertEqual(processed[0].client_msg_id, "fe-uuid-1")

    async def test_interrupt_and_replace_preserves_client_msg_id(self):
        """A front-queued message keeps its ``client_msg_id`` on the way out."""
        state = WebsocketSessionState()
        state.active_task = None
        state.active_cancel_event = None
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("replace", client_msg_id="fe-uuid-2"),
            decision=_decision("interrupt_and_replace"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].client_msg_id, "fe-uuid-2")

    # ── Per-action contract: cancel + queue-position + append ──────

    async def test_interrupt_only_sets_active_cancel_event(self):
        """``interrupt_only`` cancels the active task."""
        state = WebsocketSessionState()
        state.active_task = object()
        cancel_event = threading.Event()
        state.active_cancel_event = cancel_event
        fake_drain, _ = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("stop"),
            decision=_decision("interrupt_only"),
            start_next_if_idle=fake_drain,
        )

        self.assertTrue(cancel_event.is_set())

    async def test_interrupt_and_replace_sets_cancel_event_when_active(self):
        """``interrupt_and_replace`` cancels the active task and
        front-queues the new message; drain waits for the runner's
        ``finally`` since ``active_task`` is still non-None."""
        state = WebsocketSessionState()
        state.active_task = object()
        cancel_event = threading.Event()
        state.active_cancel_event = cancel_event
        fake_drain, processed = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("replace"),
            decision=_decision("interrupt_and_replace"),
            start_next_if_idle=fake_drain,
        )

        self.assertTrue(cancel_event.is_set())
        self.assertEqual(processed, [])
        self.assertEqual(len(state.pending_messages), 1)
        self.assertEqual(state.pending_messages[0].body, "replace")

    async def test_interrupt_and_replace_jumps_queue_ahead_of_pending(self):
        """``interrupt_and_replace`` appends at the head — anything
        already pending is processed after the replacement."""
        state = WebsocketSessionState()
        state.active_task = object()
        state.active_cancel_event = threading.Event()
        state.pending_messages.append(_msg("earlier-queued"))
        fake_drain, _ = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("replacement"),
            decision=_decision("interrupt_and_replace"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(len(state.pending_messages), 2)
        self.assertEqual(state.pending_messages[0].body, "replacement")
        self.assertEqual(state.pending_messages[1].body, "earlier-queued")

    async def test_queue_appends_at_tail_after_existing_pending(self):
        """``queue`` appends at the tail — FIFO with anything pending."""
        state = WebsocketSessionState()
        state.active_task = object()
        state.active_cancel_event = threading.Event()
        state.pending_messages.append(_msg("earlier-queued"))
        fake_drain, _ = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("newest"),
            decision=_decision("queue"),
            start_next_if_idle=fake_drain,
        )

        self.assertEqual(len(state.pending_messages), 2)
        self.assertEqual(state.pending_messages[0].body, "earlier-queued")
        self.assertEqual(state.pending_messages[1].body, "newest")

    async def test_queue_does_not_touch_cancel_event(self):
        """``queue`` leaves the active task running."""
        state = WebsocketSessionState()
        state.active_task = object()
        cancel_event = threading.Event()
        state.active_cancel_event = cancel_event
        fake_drain, _ = await self._make_drain(state)

        await _apply_arbiter_decision(
            state, message=_msg("queued"),
            decision=_decision("queue"),
            start_next_if_idle=fake_drain,
        )

        self.assertFalse(cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
