"""``process_request`` gates request-ID rendering in CLI mode on
the ``show_request_ids`` flag.

Default is False — clean prompt for first-time users. When the
operator passes ``--show-request-ids`` at startup, the ID is
appended (dim, parenthesised) to BOTH the user-input panel and
the agent-response panel so it can be copied for log lookup.

Pre-fix the User Input panel always carried the ID and the
response panel never did — this commit makes both honour the
same flag.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.llm.client import LLMResponse

try:
    from tests._runtime_fixtures import make_request_runtime
except ModuleNotFoundError:
    from _runtime_fixtures import make_request_runtime


def _make_runtime():
    return make_request_runtime()


class _OkClient:
    def __init__(self):
        self.model = "dummy/dummy-model"

    async def completion(self, messages, tools=None):
        return LLMResponse(
            text="Done.",
            tool_calls=[],
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


def _capture_panels(panel_calls):
    def fake_panel(*args, **kwargs):
        panel_calls.append((args, kwargs))
        return MagicMock()
    return fake_panel


class TestRequestIdDisplay(unittest.TestCase):
    def test_default_hides_request_id_on_both_panels(self) -> None:
        from app.coordinator import request_flow

        runtime = _make_runtime()
        runtime.llm_client = _OkClient()

        from app.cli import renderer as cli_renderer
        try:
            from tests._runtime_fixtures import wire_cli_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_cli_test_bus
        panel_calls: list = []
        with patch.object(cli_renderer, "Panel", _capture_panels(panel_calls)):
            request_flow.process_request(
                runtime=runtime,
                user_input="hi",
                cancel_event=None,
                event_bus=wire_cli_test_bus(show_request_ids=False),
            )

        rendered = "\n".join(str(a) for args, _ in panel_calls for a in args)
        self.assertIn("User Input", rendered)
        self.assertIn("Swarpius", rendered)
        # Default: no rq-cNN-NNNN visible anywhere.
        self.assertNotIn("rq-c", rendered)

    def test_flag_on_shows_request_id_on_both_panels(self) -> None:
        from app.coordinator import request_flow

        runtime = _make_runtime()
        runtime.llm_client = _OkClient()

        from app.cli import renderer as cli_renderer
        try:
            from tests._runtime_fixtures import wire_cli_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_cli_test_bus
        panel_calls: list = []
        with patch.object(cli_renderer, "Panel", _capture_panels(panel_calls)):
            request_flow.process_request(
                runtime=runtime,
                user_input="hi",
                cancel_event=None,
                event_bus=wire_cli_test_bus(show_request_ids=True),
            )

        # Request ID must appear at least twice — once on the user
        # input panel and once on the agent response panel.
        rendered = "\n".join(str(a) for args, _ in panel_calls for a in args)
        self.assertGreaterEqual(rendered.count("rq-c"), 2)


if __name__ == "__main__":
    unittest.main()
