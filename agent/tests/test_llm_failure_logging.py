"""LLMClient.completion logs WARNING with traceback on exception, and
sets an explicit ``timeout`` on every call so stuck provider calls
fail fast and visibly.
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.llm.client import LLMClient


class _FakeAuthError(Exception):
    pass


def _fake_litellm(raises=None, response=None, recorder=None) -> SimpleNamespace:
    """Stand-in litellm whose ``acompletion`` either raises or returns
    the canned response, and records the kwargs it was called with.
    """
    async def _acompletion(**kwargs):
        if recorder is not None:
            recorder["kwargs"] = kwargs
        if raises is not None:
            raise raises
        return response

    return SimpleNamespace(
        acompletion=_acompletion,
        AuthenticationError=_FakeAuthError,
        NotFoundError=type("_NotFoundError", (Exception,), {}),
        RateLimitError=type("_RateLimitError", (Exception,), {}),
        callbacks=None,
        success_callback=[],
        failure_callback=[],
        drop_params=False,
        completion_cost=lambda **_: 0.0,
        suppress_debug_info=False,
    )


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
            )
        ],
        usage=None,
    )


def _run(coro):
    return asyncio.run(coro)


class TestLLMClientExceptionLogging(unittest.TestCase):
    """When ``acompletion`` raises, the exception must be visible in
    ``swarpius.log`` with its class and message."""

    def test_exception_is_logged_at_warning_with_class_and_message(self):
        fake_litellm = _fake_litellm(raises=_FakeAuthError("401 invalid key"))
        client = LLMClient(model="anthropic/claude-x", api_key="sk-ant")
        with (
            patch.dict("sys.modules", {"litellm": fake_litellm}, clear=False),
            self.assertLogs("swarpius.llm_client", level=logging.WARNING) as cap,
            self.assertRaises(_FakeAuthError),
        ):
            _run(client.completion(messages=[{"role": "user", "content": "hi"}]))

        msgs = "\n".join(cap.output)
        self.assertIn("_FakeAuthError", msgs)
        self.assertIn("401 invalid key", msgs)



class TestLLMClientTimeout(unittest.TestCase):
    """All three agents (coordinator, arbiter, diagnostic) share
    ``LLMClient.completion`` — an explicit timeout is passed on every
    call, configurable via ``LLM_TIMEOUT_SECONDS`` (default 60)."""

    def test_request_uses_60_second_timeout_by_default(self):
        recorder: dict = {}
        fake_litellm = _fake_litellm(response=_ok_response(), recorder=recorder)
        client = LLMClient(model="anthropic/claude-x", api_key="sk-ant")
        with patch.dict("sys.modules", {"litellm": fake_litellm}, clear=False):
            _run(client.completion(messages=[{"role": "user", "content": "hi"}]))

        self.assertIn("timeout", recorder["kwargs"])
        self.assertEqual(recorder["kwargs"]["timeout"], 60)

    def test_request_honours_LLM_TIMEOUT_SECONDS_env(self):
        import os as _os

        from app.settings import core as settings_core

        recorder: dict = {}
        fake_litellm = _fake_litellm(response=_ok_response(), recorder=recorder)
        client = LLMClient(model="anthropic/claude-x", api_key="sk-ant")
        with (
            patch.dict(_os.environ, {"LLM_TIMEOUT_SECONDS": "3"}, clear=False),
            patch.dict("sys.modules", {"litellm": fake_litellm}, clear=False),
        ):
            settings_core.reset_settings_for_tests()
            try:
                _run(client.completion(messages=[{"role": "user", "content": "hi"}]))
            finally:
                settings_core.reset_settings_for_tests()

        self.assertEqual(recorder["kwargs"]["timeout"], 3)


class TestLogLlmFailure(unittest.TestCase):
    """The shared agent-failure logger: concise (no traceback) for
    routine provider errors / timeouts, full traceback only for the
    unexpected; the line names the source and the failure."""

    def _known_exc(self):
        # Class name matches _KNOWN_LLM_EXCEPTION_NAMES.
        return type("RateLimitError", (Exception,), {})("429 slow down")

    def test_known_exception_concise_no_traceback_at_warning(self):
        from app.coordinator.request_flow import _log_llm_failure
        log = logging.getLogger("test.llmfail.known")
        with self.assertLogs(log, level=logging.WARNING) as cap:
            ret = _log_llm_failure(
                log, "Diagnostic agent (classification)", self._known_exc(), fatal=False,
            )
        self.assertEqual(len(cap.records), 1)
        rec = cap.records[0]
        self.assertEqual(rec.levelno, logging.WARNING)
        self.assertIsNone(rec.exc_info)  # no traceback for a known error
        self.assertIn("Diagnostic agent (classification)", rec.getMessage())
        self.assertIn("RateLimitError", rec.getMessage())
        self.assertIn("429 slow down", ret)

    def test_bare_timeout_error_is_treated_as_known(self):
        from app.coordinator.request_flow import _log_llm_failure
        log = logging.getLogger("test.llmfail.timeout")
        with self.assertLogs(log, level=logging.WARNING) as cap:
            _log_llm_failure(
                log, "Diagnostic agent (classification)", TimeoutError("5s budget"), fatal=False,
            )
        rec = cap.records[0]
        self.assertIsNone(rec.exc_info)
        self.assertIn("TimeoutError", rec.getMessage())

    def test_unexpected_exception_logs_traceback_at_error(self):
        from app.coordinator.request_flow import _log_llm_failure
        log = logging.getLogger("test.llmfail.unexpected")
        with self.assertLogs(log, level=logging.ERROR) as cap:
            _log_llm_failure(
                log, "Arbiter (interrupt decision)", ValueError("boom"), fatal=False,
            )
        rec = cap.records[0]
        self.assertEqual(rec.levelno, logging.ERROR)
        self.assertIsNotNone(rec.exc_info)  # full trace for the unexpected
        self.assertIn("Arbiter (interrupt decision)", rec.getMessage())

    def test_fatal_known_error_logs_at_error_level(self):
        from app.coordinator.request_flow import _log_llm_failure
        log = logging.getLogger("test.llmfail.fatal")
        with self.assertLogs(log, level=logging.ERROR) as cap:
            _log_llm_failure(
                log, "Coordinator tool loop", self._known_exc(), fatal=True,
            )
        rec = cap.records[0]
        self.assertEqual(rec.levelno, logging.ERROR)
        self.assertIsNone(rec.exc_info)  # still concise — known error, just higher severity


if __name__ == "__main__":
    unittest.main()
