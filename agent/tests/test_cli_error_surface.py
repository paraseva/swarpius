"""``process_request`` must render a user-facing error Panel in CLI
mode when the tool loop raises.

Pre-fix, errors only surfaced via WS (``CHANNEL_ERRORS``); a CLI
user saw nothing actionable in their terminal — just the raw
traceback the logger dumped to stderr — and the prompt returned
as if the request had completed.

The Panel mirrors the clean summary built by
``_summarise_tool_loop_error`` so an LLM provider hiccup or rate
limit reads as ``RateLimitError: 429 Too Many Requests`` rather
than a wall of traceback.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.llm.client import LLMResponse

try:
    from tests._runtime_fixtures import make_request_runtime as _make_runtime
except ModuleNotFoundError:
    from _runtime_fixtures import make_request_runtime as _make_runtime


class _RaisingLLMClient:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.model = "dummy/dummy-model"

    async def completion(self, messages, tools=None):
        raise self._exc


class _OkLLMClient:
    def __init__(self):
        self.model = "dummy/dummy-model"

    async def completion(self, messages, tools=None):
        return LLMResponse(
            text="Hi.",
            tool_calls=[],
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


class TestCliErrorSurface(unittest.TestCase):
    def test_tool_loop_error_renders_panel_in_cli(self) -> None:
        from app.coordinator import request_flow

        runtime = _make_runtime()
        runtime.llm_client = _RaisingLLMClient(RuntimeError("provider unreachable"))

        # Capture Panel construction args directly — the test stubs
        # discard the body so we can't fish it back out of the
        # console mock.
        panel_calls: list[tuple] = []

        def fake_panel(*args, **kwargs):
            panel_calls.append((args, kwargs))
            return MagicMock()

        from app.cli import renderer as cli_renderer
        try:
            from tests._runtime_fixtures import wire_cli_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_cli_test_bus
        with patch.object(cli_renderer, "Panel", fake_panel):
            request_flow.process_request(
                runtime=runtime,
                user_input="hi",
                cancel_event=None,
                event_bus=wire_cli_test_bus(),
            )

        rendered = "\n".join(
            str(a) for args, _ in panel_calls for a in args
        )
        self.assertIn("RuntimeError", rendered)
        self.assertIn("provider unreachable", rendered)
        self.assertIn("Request failed", rendered)

    def test_no_error_panel_on_successful_request(self) -> None:
        """Belt-and-braces: the new error path mustn't fire on a
        clean run."""
        from app.coordinator import request_flow

        runtime = _make_runtime()
        runtime.llm_client = _OkLLMClient()

        panel_calls: list[tuple] = []

        def fake_panel(*args, **kwargs):
            panel_calls.append((args, kwargs))
            return MagicMock()

        from app.cli import renderer as cli_renderer
        try:
            from tests._runtime_fixtures import wire_cli_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_cli_test_bus
        with patch.object(cli_renderer, "Panel", fake_panel):
            request_flow.process_request(
                runtime=runtime,
                user_input="hi",
                cancel_event=None,
                event_bus=wire_cli_test_bus(),
            )

        rendered = "\n".join(
            str(a) for args, _ in panel_calls for a in args
        )
        self.assertNotIn("Request failed", rendered)


if __name__ == "__main__":
    unittest.main()
