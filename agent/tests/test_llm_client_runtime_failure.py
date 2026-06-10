"""LLMClient.completion flags runtime auth / model-not-found failures
on the ConfigValidator so the UI can surface revoked keys without the
user clicking Save & Validate.

The exception still re-raises so the caller's existing error path
runs unchanged — the only side effect is the validator update.
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.llm.client import LLMClient
from app.settings import validation as config_validation


class _FakeAuthError(Exception):
    pass


class _FakeNotFoundError(Exception):
    pass


class _FakeRateLimitError(Exception):
    pass


class _FakeBadRequestError(Exception):
    pass


def _fake_litellm(raises: Exception) -> SimpleNamespace:
    """Stand-in litellm module that raises the given exception on
    ``acompletion``. Includes the exception-class attributes the hook
    reads via getattr."""
    async def _acompletion(**_kwargs):
        raise raises
    return SimpleNamespace(
        acompletion=_acompletion,
        AuthenticationError=_FakeAuthError,
        NotFoundError=_FakeNotFoundError,
        RateLimitError=_FakeRateLimitError,
        BadRequestError=_FakeBadRequestError,
        callbacks=None,
        success_callback=[],
        failure_callback=[],
        drop_params=False,
        completion_cost=lambda **_: 0.0,
    )


def _run(coro):
    return asyncio.run(coro)


class TestRuntimeFailureFlagsValidator(unittest.TestCase):
    """When acompletion raises an auth / not-found exception, the
    validator's matching agent row flips to ok=False before the
    exception propagates."""

    def setUp(self):
        config_validation.reset_validator_for_tests()

    def tearDown(self):
        config_validation.reset_validator_for_tests()

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_authentication_error_flags_provider(self):
        # Seed the validator with a passed-state for anthropic so the
        # flip is observable.
        validator = config_validation.get_validator()
        with patch("requests.get") as mock_get:
            mock_get.return_value = SimpleNamespace(
                status_code=200,
                json=lambda: {"data": [{"id": "claude-x"}]},
            )
            _run(validator.validate())

        fake_litellm = _fake_litellm(_FakeAuthError("401 invalid key"))
        client = LLMClient(
            model="anthropic/claude-x",
            api_key="sk-ant",

        )
        with patch.dict(
            "sys.modules", {"litellm": fake_litellm}, clear=False,
        ):
            with self.assertRaises(_FakeAuthError):
                _run(client.completion(messages=[{"role": "user", "content": "hi"}]))

        coord = next(
            r for r in validator.current().results if r.agent == "coordinator"
        )
        self.assertFalse(coord.ok)
        self.assertEqual(coord.error_kind, "auth_failed")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_not_found_error_flags_provider(self):
        validator = config_validation.get_validator()
        with patch("requests.get") as mock_get:
            mock_get.return_value = SimpleNamespace(
                status_code=200,
                json=lambda: {"data": [{"id": "claude-x"}]},
            )
            _run(validator.validate())

        fake_litellm = _fake_litellm(_FakeNotFoundError("model gone"))
        client = LLMClient(
            model="anthropic/claude-x",
            api_key="sk-ant",

        )
        with patch.dict(
            "sys.modules", {"litellm": fake_litellm}, clear=False,
        ):
            with self.assertRaises(_FakeNotFoundError):
                _run(client.completion(messages=[{"role": "user", "content": "hi"}]))

        coord = next(
            r for r in validator.current().results if r.agent == "coordinator"
        )
        self.assertFalse(coord.ok)
        self.assertEqual(coord.error_kind, "not_found")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_transient_error_does_not_flag(self):
        """Rate-limit isn't a config problem — the validator should
        stay PASSED rather than flapping on a hot transient."""
        validator = config_validation.get_validator()
        with patch("requests.get") as mock_get:
            mock_get.return_value = SimpleNamespace(
                status_code=200,
                json=lambda: {"data": [{"id": "claude-x"}]},
            )
            _run(validator.validate())

        fake_litellm = _fake_litellm(_FakeRateLimitError("429"))
        client = LLMClient(
            model="anthropic/claude-x",
            api_key="sk-ant",

        )
        with patch.dict(
            "sys.modules", {"litellm": fake_litellm}, clear=False,
        ):
            with self.assertRaises(_FakeRateLimitError):
                _run(client.completion(messages=[{"role": "user", "content": "hi"}]))

        coord = next(
            r for r in validator.current().results if r.agent == "coordinator"
        )
        self.assertTrue(coord.ok)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_bad_request_error_flags_provider(self):
        """A deprecated/unsupported param (400 bad-request) is a permanent
        config problem — flip the provider to failed so the user sees it
        instead of it failing silently."""
        validator = config_validation.get_validator()
        with patch("requests.get") as mock_get:
            mock_get.return_value = SimpleNamespace(
                status_code=200,
                json=lambda: {"data": [{"id": "claude-x"}]},
            )
            _run(validator.validate())

        fake_litellm = _fake_litellm(
            _FakeBadRequestError("`temperature` is deprecated for this model."),
        )
        client = LLMClient(
            model="anthropic/claude-x",
            api_key="sk-ant",
        )
        with patch.dict(
            "sys.modules", {"litellm": fake_litellm}, clear=False,
        ):
            with self.assertRaises(_FakeBadRequestError):
                _run(client.completion(messages=[{"role": "user", "content": "hi"}]))

        coord = next(
            r for r in validator.current().results if r.agent == "coordinator"
        )
        self.assertFalse(coord.ok)
        self.assertEqual(coord.error_kind, "bad_request")


if __name__ == "__main__":
    unittest.main()
