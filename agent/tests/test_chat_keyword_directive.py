"""Contract for ``_handle_keyword_directive``: directive bodies emit
``control_command_acknowledged`` on agent-outputs (carrying the
``client_msg_id``), cancel the active task, and short-circuit the
regular request flow. Non-directive bodies pass through untouched.
"""

from __future__ import annotations

import threading
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.constants import (  # noqa: E402
    CHANNEL_AGENT_OUTPUTS,
    CHANNEL_LLM_DIAGNOSTICS,
)
from app.io.websocket_flow import (  # noqa: E402
    WebsocketSessionState,
    _handle_keyword_directive,
)


def _agent_outputs_payload(calls):
    return next(p for (ch, p) in calls if ch == CHANNEL_AGENT_OUTPUTS)


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, channel: str, payload: dict, **_kwargs) -> None:
        self.calls.append((channel, payload))


class TestHandleKeywordDirective(unittest.TestCase):

    def test_cancel_emits_control_command_acknowledged_with_client_msg_id(self):
        state = WebsocketSessionState()
        state.active_task = object()
        state.active_cancel_event = threading.Event()
        send = _Capture()

        handled = _handle_keyword_directive(
            body="cancel",
            client_msg_id="fe-uuid-1",
            state=state,
            ws_send_fn=send,
        )

        self.assertTrue(handled)
        payload = _agent_outputs_payload(send.calls)
        self.assertEqual(payload["event_type"], "control_command_acknowledged")
        self.assertEqual(payload["client_msg_id"], "fe-uuid-1")
        self.assertEqual(payload["action"], "interrupt_only")

    def test_cancel_prefix_is_recognised(self):
        state = WebsocketSessionState()
        send = _Capture()

        handled = _handle_keyword_directive(
            body="cancel that please",
            client_msg_id="fe-uuid-2",
            state=state,
            ws_send_fn=send,
        )

        self.assertTrue(handled)
        payload = _agent_outputs_payload(send.calls)
        self.assertEqual(payload["client_msg_id"], "fe-uuid-2")

    def test_directive_cancels_active_task(self):
        state = WebsocketSessionState()
        state.active_task = object()
        state.active_cancel_event = threading.Event()
        send = _Capture()

        _handle_keyword_directive(
            body="cancel",
            client_msg_id="x",
            state=state,
            ws_send_fn=send,
        )

        self.assertTrue(state.active_cancel_event.is_set())

    def test_directive_does_not_append_to_pending(self):
        state = WebsocketSessionState()
        send = _Capture()

        _handle_keyword_directive(
            body="cancel",
            client_msg_id="x",
            state=state,
            ws_send_fn=send,
        )

        self.assertEqual(len(state.pending_messages), 0)

    def test_no_active_task_still_acks(self):
        # The pill is rendered regardless of whether anything was
        # cancellable, so the user sees the directive landed.
        state = WebsocketSessionState()
        state.active_task = None
        state.active_cancel_event = None
        send = _Capture()

        handled = _handle_keyword_directive(
            body="cancel", client_msg_id="x",
            state=state, ws_send_fn=send,
        )

        self.assertTrue(handled)
        agent_calls = [c for c in send.calls if c[0] == CHANNEL_AGENT_OUTPUTS]
        self.assertEqual(len(agent_calls), 1)

    def test_keyword_directive_emits_interrupt_decision(self):
        """Keyword catches bypass the arbiter, but the "Last Interrupt
        Decision" panel section should still record them — the user
        cancelled, that's a decision worth surfacing."""
        state = WebsocketSessionState()
        send = _Capture()

        _handle_keyword_directive(
            body="cancel",
            client_msg_id="x",
            state=state,
            ws_send_fn=send,
        )

        diag_payloads = [p for (ch, p) in send.calls if ch == CHANNEL_LLM_DIAGNOSTICS]
        self.assertEqual(len(diag_payloads), 1)
        decision = diag_payloads[0]
        self.assertEqual(decision["event_type"], "interrupt_decision")
        self.assertEqual(decision["decision_source"], "keyword")
        self.assertEqual(decision["action"], "interrupt_only")
        self.assertIn("cancel", decision["reason"])

    def test_non_directive_body_is_passthrough(self):
        state = WebsocketSessionState()
        send = _Capture()

        handled = _handle_keyword_directive(
            body="play some music",
            client_msg_id="x",
            state=state,
            ws_send_fn=send,
        )

        self.assertFalse(handled)
        self.assertEqual(send.calls, [])

    def test_stop_playing_is_not_directive(self):
        # ``stop`` and ``stop playing`` both fall through to the LLM:
        # neither is in the keyword set, so the transport command
        # reaches the coordinator which routes it to Roon.
        state = WebsocketSessionState()
        send = _Capture()

        handled = _handle_keyword_directive(
            body="stop playing",
            client_msg_id="x",
            state=state,
            ws_send_fn=send,
        )

        self.assertFalse(handled)

    def test_client_msg_id_optional(self):
        # Without one, the ack still fires; the payload simply omits
        # the field and the FE has no specific outbound to mark.
        state = WebsocketSessionState()
        send = _Capture()

        handled = _handle_keyword_directive(
            body="cancel", client_msg_id=None,
            state=state, ws_send_fn=send,
        )

        self.assertTrue(handled)
        payload = _agent_outputs_payload(send.calls)
        self.assertNotIn("client_msg_id", payload)

    # ── Negative-space: non-directive must not mutate state ───────

    def test_non_directive_does_not_set_cancel_event(self):
        """A non-directive body leaves ``active_cancel_event`` alone."""
        state = WebsocketSessionState()
        state.active_task = object()
        cancel_event = threading.Event()
        state.active_cancel_event = cancel_event
        send = _Capture()

        _handle_keyword_directive(
            body="play some music", client_msg_id="x",
            state=state, ws_send_fn=send,
        )

        self.assertFalse(cancel_event.is_set())

    def test_non_directive_leaves_pending_messages_untouched(self):
        """A non-directive body leaves ``pending_messages`` alone."""
        from app.io.websocket_flow import ChatMessage
        state = WebsocketSessionState()
        existing = ChatMessage(body="earlier", client_msg_id="prev")
        state.pending_messages.append(existing)
        send = _Capture()

        _handle_keyword_directive(
            body="play some music", client_msg_id="x",
            state=state, ws_send_fn=send,
        )

        self.assertEqual(len(state.pending_messages), 1)
        self.assertIs(state.pending_messages[0], existing)

    def test_directive_payload_carries_session_control_source(self):
        """Payload carries ``source: "[Session Control]"`` — the
        agent-outputs label convention used by other handlers."""
        state = WebsocketSessionState()
        send = _Capture()

        _handle_keyword_directive(
            body="cancel", client_msg_id="x",
            state=state, ws_send_fn=send,
        )

        payload = _agent_outputs_payload(send.calls)
        self.assertEqual(payload["source"], "[Session Control]")

    def test_every_listed_keyword_is_recognised(self):
        """Every entry in ``cancellation._CANCEL_EXACT`` /
        ``_CANCEL_PREFIX`` resolves to a directive."""
        for body in (
            "cancel", "abort", "nevermind", "never mind", "quit",
            "cancel that", "abort everything", "quit doing that",
        ):
            with self.subTest(body=body):
                state = WebsocketSessionState()
                send = _Capture()
                handled = _handle_keyword_directive(
                    body=body, client_msg_id="x",
                    state=state, ws_send_fn=send,
                )
                self.assertTrue(handled, f"{body!r} should be a directive")
                agent_calls = [c for c in send.calls if c[0] == CHANNEL_AGENT_OUTPUTS]
                self.assertEqual(len(agent_calls), 1)


if __name__ == "__main__":
    unittest.main()
