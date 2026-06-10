"""Process_request must invoke its tts_say_fn callback in CLI mode so
spoken output reaches the user. Pairs with the unit tests for
``AppIO.sayit`` in test_cli_tts_emission.py."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

try:
    from tests._runtime_fixtures import (
        make_request_runtime as _make_request_runtime,
    )
    from tests._runtime_fixtures import (
        wire_cli_test_bus,
    )
except ModuleNotFoundError:
    from _runtime_fixtures import (  # type: ignore[no-redef]
        make_request_runtime as _make_request_runtime,
    )
    from _runtime_fixtures import (
        wire_cli_test_bus,
    )


class TestCliTtsWiring(unittest.TestCase):
    def test_cli_mode_invokes_tts_say_fn_with_final_response(self):
        from app.coordinator.request_flow import process_request
        from app.llm.client import LLMResponse

        runtime = _make_request_runtime()

        async def _fake_completion(messages, tools=None):
            return LLMResponse(
                text="Playing OK Computer on Kitchen.",
                tool_calls=[],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _fake_completion

        spoken: list[tuple[str, str]] = []

        def fake_say(agent_name: str, message: str) -> None:
            spoken.append((agent_name, message))

        process_request(
            runtime=runtime,
            user_input="play OK Computer",
            cancel_event=None,
            event_bus=wire_cli_test_bus(tts_say_fn=fake_say),
        )

        self.assertEqual(len(spoken), 1, f"expected one TTS call, got {spoken}")
        agent_name, message = spoken[0]
        self.assertEqual(agent_name, "Coordinator")
        self.assertIn("OK Computer", message)

    def test_cli_mode_with_no_tts_fn_does_not_raise(self):
        """tts_say_fn is optional — process_request should still run
        cleanly when it's omitted."""
        from app.coordinator.request_flow import process_request
        from app.llm.client import LLMResponse

        runtime = _make_request_runtime()

        async def _fake_completion(messages, tools=None):
            return LLMResponse(
                text="Done.",
                tool_calls=[],
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _fake_completion

        process_request(
            runtime=runtime,
            user_input="hi",
            cancel_event=None,
            event_bus=wire_cli_test_bus(),  # no tts_say_fn passed
        )

    def test_chat_panel_agents_constant_matches_emitted_agent_name(self):
        """``AppIO.sayit`` gates TTS on ``agent_name in chat_panel_agents``.
        These two strings must stay in lockstep or TTS silently skips."""
        from app.constants import CHAT_PANEL_AGENTS
        self.assertIn("Coordinator", CHAT_PANEL_AGENTS)


if __name__ == "__main__":
    unittest.main()
