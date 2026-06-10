"""Error-taxonomy contract for ``llm_completion``.

Callers need to distinguish transient (retry-worthy), permanent
(misconfig — fail fast), and input-shape (context too large — fail
gracefully) errors. These tests pin that distinguishability so the
implementation can't collapse all three to a blanket ``None``.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ANALYSER_DIR = Path(__file__).resolve().parents[2] / "passive-analyser"
if str(ANALYSER_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSER_DIR))

from analyser import (
    analyse,  # noqa: E402
    llm_layer,  # noqa: E402
)


def _patch_litellm(completion_mock):
    """Swap llm_layer.litellm for a MagicMock whose .completion is the
    given mock. Avoids loading the real litellm (which has an aiohttp
    import issue in this venv)."""
    fake_litellm = MagicMock()
    fake_litellm.completion = completion_mock
    fake_litellm.success_callback = []
    fake_litellm.failure_callback = []
    return patch.object(llm_layer, "litellm", fake_litellm)


def _call_llm_completion():
    """Invoke llm_completion with dummy args — only the mocked litellm
    response shape matters."""
    return analyse.llm_completion(
        model="anthropic/claude-sonnet-4-6",
        api_key="dummy",
        system="system prompt",
        user_message="user message",
    )


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class TestHappyPath(unittest.TestCase):
    def test_successful_completion_returns_text(self):
        completion = MagicMock(return_value=_FakeResponse("ok"))
        with _patch_litellm(completion):
            result = _call_llm_completion()
        # Under current code, happy path returns the text string; post-
        # refactor it returns something with text="ok" and no error_kind.
        # Accept either shape for the pin.
        if isinstance(result, str):
            self.assertEqual(result, "ok")
        else:
            self.assertEqual(getattr(result, "text", None), "ok")
            self.assertIsNone(getattr(result, "error_kind", None))


class TestErrorDistinguishability(unittest.TestCase):
    """Caller should be able to tell what went wrong."""

    def test_rate_limit_is_transient(self):
        """Rate-limit style error must be flagged as transient so the
        caller can retry with backoff."""
        class RateLimitError(Exception):
            pass

        completion = MagicMock(side_effect=RateLimitError("429 too many requests"))
        with _patch_litellm(completion):
            result = _call_llm_completion()

        # Post-refactor: result has error_kind == "transient"
        self.assertIsNotNone(result, "result collapsed to None — caller can't retry")
        self.assertEqual(getattr(result, "error_kind", None), "transient")

    def test_auth_error_is_permanent(self):
        """Auth / misconfig must be flagged permanent — callers should
        NOT retry blindly."""
        class AuthError(Exception):
            pass

        completion = MagicMock(side_effect=AuthError("invalid API key"))
        with _patch_litellm(completion):
            result = _call_llm_completion()

        self.assertIsNotNone(result, "result collapsed to None — can't distinguish from transient")
        self.assertEqual(getattr(result, "error_kind", None), "permanent")

    def test_context_length_is_input_shape(self):
        """Context-too-large must be flagged input_shape — caller should
        consider truncating input rather than retrying as-is."""
        class ContextLengthError(Exception):
            pass

        completion = MagicMock(side_effect=ContextLengthError("maximum context length exceeded"))
        with _patch_litellm(completion):
            result = _call_llm_completion()

        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "error_kind", None), "input_shape")

    def test_bad_request_is_permanent(self):
        """A 400 bad-request (deprecated/unsupported param, unknown model)
        is a config error that won't fix itself — flag permanent so it
        halts and surfaces rather than retrying every scan."""
        class BadRequestError(Exception):
            pass

        completion = MagicMock(side_effect=BadRequestError(
            "AnthropicException - invalid_request_error: `temperature` is "
            "deprecated for this model.",
        ))
        with _patch_litellm(completion):
            result = _call_llm_completion()

        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "error_kind", None), "permanent")


if __name__ == "__main__":
    unittest.main()
