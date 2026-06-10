"""Contract: ``arbitrate_interrupt`` emits llm-diagnostics events.

When ``ws_send_fn`` is provided, the arbiter emits a call_started +
call_completed pair on the llm-diagnostics channel — mirroring the
diagnostic-classification flow so the frontend diagnostics panel
shows the arbiter's LLM call alongside the other agents'.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from app.constants import CHANNEL_LLM_DIAGNOSTICS
from app.llm.client import LLMResponse


def _make_runtime(arbiter_client=None) -> object:
    from app.runtime.state import RuntimeState

    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
        # Arbiter is opt-in (default off). The tests below exercise
        # the arbiter's emission path, so enable it explicitly.
        "ENABLE_INTERRUPT_ARBITER": "true",
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
        rs.arbiter_client = arbiter_client
    return rs


def _canned_arbiter_response() -> LLMResponse:
    return LLMResponse(text='{"action": "queue", "reason": "test", "confidence": 0.5}')


class TestArbiterLlmDiagnostics(unittest.TestCase):

    def test_successful_arbiter_call_emits_call_started_and_completed(self) -> None:
        """The arbiter is an LLM call that takes observable time — it
        must pair with the diagnostics UI the same way the diagnostic
        agent does.
        """
        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"
        async def _fake_completion(messages, tools=None):
            return _canned_arbiter_response()
        arbiter_client.completion = _fake_completion

        runtime = _make_runtime(arbiter_client=arbiter_client)

        ws_calls: list[tuple[str, dict]] = []

        def spy(channel: str, payload: dict) -> None:
            ws_calls.append((channel, payload))

        arbitrate_interrupt(runtime, "old req", "new req", ws_send_fn=spy)

        diag_events = [p for (ch, p) in ws_calls if ch == CHANNEL_LLM_DIAGNOSTICS]
        event_types = [p.get("event_type") for p in diag_events]
        self.assertIn("call_started", event_types)
        self.assertIn("call_completed", event_types)

        started = next(p for p in diag_events if p["event_type"] == "call_started")
        completed = next(p for p in diag_events if p["event_type"] == "call_completed")
        self.assertEqual(
            started["call_id"], completed["call_id"],
            "call_started and call_completed must share a call_id",
        )
        self.assertEqual(started.get("agent_name"), "Arbiter")
        self.assertEqual(started.get("model"), "dummy/dummy-model")

    def test_failed_arbiter_llm_call_emits_call_failed_with_error(self) -> None:
        """When the arbiter's LLM call raises, the diagnostics stream
        must fire ``call_failed`` carrying the exception message — not
        ``call_completed`` — so the panel can render the failure
        reason inline instead of silently clearing the spinner.
        """
        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"
        async def _boom(messages, tools=None):
            raise RuntimeError("provider is on fire")
        arbiter_client.completion = _boom

        runtime = _make_runtime(arbiter_client=arbiter_client)

        ws_calls: list[tuple[str, dict]] = []
        arbitrate_interrupt(
            runtime, "old req", "new req",
            ws_send_fn=lambda ch, p: ws_calls.append((ch, p)),
        )

        diag_events = [p for (ch, p) in ws_calls if ch == CHANNEL_LLM_DIAGNOSTICS]
        types = [p.get("event_type") for p in diag_events]
        self.assertIn("call_failed", types)
        self.assertNotIn("call_completed", types)
        failed = next(p for p in diag_events if p["event_type"] == "call_failed")
        self.assertIn("provider is on fire", failed.get("error", ""))

    def test_unparseable_response_emits_call_failed(self) -> None:
        """A parse failure on the arbiter's response is a failure of
        the call — ``call_failed`` must fire with an error string that
        mentions the unparseable payload."""
        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"
        bad_text = "not JSON, sorry"

        async def _bad(messages, tools=None):
            return LLMResponse(text=bad_text)
        arbiter_client.completion = _bad

        runtime = _make_runtime(arbiter_client=arbiter_client)

        ws_calls: list[tuple[str, dict]] = []
        arbitrate_interrupt(
            runtime, "old req", "new req",
            ws_send_fn=lambda ch, p: ws_calls.append((ch, p)),
        )

        diag_events = [p for (ch, p) in ws_calls if ch == CHANNEL_LLM_DIAGNOSTICS]
        types = [p.get("event_type") for p in diag_events]
        self.assertIn("call_failed", types)
        self.assertNotIn("call_completed", types)

    def test_unparseable_response_logs_raw_text_at_warning(self) -> None:
        """When the arbiter response can't be JSON-decoded or
        validated, the raw text must hit ``swarpius.log`` at WARNING
        with the failing payload so investigators can see what the
        model actually returned."""
        import logging as _logging

        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"
        bad_text = "I think we should queue this one"

        async def _bad(messages, tools=None):
            return LLMResponse(text=bad_text)
        arbiter_client.completion = _bad

        runtime = _make_runtime(arbiter_client=arbiter_client)

        with self.assertLogs("swarpius.request_flow", level=_logging.WARNING) as cap:
            result = arbitrate_interrupt(
                runtime, "old", "new", ws_send_fn=lambda ch, p: None,
            )

        msgs = "\n".join(cap.output)
        self.assertIn(bad_text, msgs)
        self.assertEqual(result.action, "queue")

    def test_empty_response_text_logs_at_warning(self) -> None:
        """An empty / None ``response.text`` from the arbiter is also
        a failure — log it so the silent fall-through is visible."""
        import logging as _logging

        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"

        async def _empty(messages, tools=None):
            return LLMResponse(text=None)
        arbiter_client.completion = _empty

        runtime = _make_runtime(arbiter_client=arbiter_client)

        with self.assertLogs("swarpius.request_flow", level=_logging.WARNING) as cap:
            result = arbitrate_interrupt(
                runtime, "old", "new", ws_send_fn=lambda ch, p: None,
            )

        msgs = "\n".join(cap.output).lower()
        self.assertTrue("no text" in msgs or "empty" in msgs,
                        f"expected an empty-text warning, got: {cap.output}")
        self.assertEqual(result.action, "queue")

    def test_ws_send_fn_none_is_backward_compatible(self) -> None:
        """Tests and CLI mode may not have a ws_send_fn. Passing None
        must not raise.
        """
        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"
        async def _fake_completion(messages, tools=None):
            return _canned_arbiter_response()
        arbiter_client.completion = _fake_completion

        runtime = _make_runtime(arbiter_client=arbiter_client)

        result = arbitrate_interrupt(runtime, "old", "new", ws_send_fn=None)
        self.assertEqual(result.action, "queue")

    def test_successful_decision_is_logged(self) -> None:
        """The arbiter's decision must reach the server log on success,
        not just the WS diagnostics stream — otherwise a clean decision
        is invisible in swarpius.log (only failures were logged before).
        """
        from app.coordinator.request_flow import arbitrate_interrupt

        arbiter_client = MagicMock()
        arbiter_client.model = "dummy/dummy-model"
        async def _fake_completion(messages, tools=None):
            return _canned_arbiter_response()
        arbiter_client.completion = _fake_completion

        runtime = _make_runtime(arbiter_client=arbiter_client)

        with self.assertLogs("swarpius.request_flow", level="INFO") as cm:
            arbitrate_interrupt(runtime, "old req", "new req", ws_send_fn=None)

        joined = "\n".join(cm.output)
        self.assertIn("Arbiter decision", joined)
        self.assertIn("queue", joined)


if __name__ == "__main__":
    unittest.main()
