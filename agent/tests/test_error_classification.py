"""The single, shared LLM-error classifier used by every agent.

One taxonomy: each agent maps the kind to its own action (the coordinator
flags the validator + surfaces; the analyser retries transient / halts
permanent), but the classification itself lives in exactly one place.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.llm.error_classification import classify_llm_error, is_permanent  # noqa: E402


def _named(name: str, message: str = "") -> Exception:
    """An exception whose class name drives the name-based fallback path."""
    return type(name, (Exception,), {})(message)


class TestClassifyByName(unittest.TestCase):
    """Name / message fallback (no litellm types available)."""

    def test_auth(self):
        self.assertEqual(classify_llm_error(_named("AuthenticationError", "401")), "auth")

    def test_not_found(self):
        self.assertEqual(classify_llm_error(_named("NotFoundError", "model gone")), "not_found")

    def test_rate_limited(self):
        self.assertEqual(classify_llm_error(_named("RateLimitError", "429")), "rate_limited")

    def test_context_length(self):
        self.assertEqual(
            classify_llm_error(_named("ContextWindowExceededError", "maximum context")),
            "context_length",
        )

    def test_bad_request(self):
        self.assertEqual(
            classify_llm_error(_named(
                "BadRequestError",
                "invalid_request_error: `temperature` is deprecated for this model.",
            )),
            "bad_request",
        )

    def test_unknown_defaults_transient(self):
        self.assertEqual(classify_llm_error(_named("APIConnectionError", "blip")), "transient")


class TestClassifyByType(unittest.TestCase):
    """isinstance path against a litellm-like module (the coordinator's
    robust route)."""

    def _litellm(self):
        return SimpleNamespace(
            AuthenticationError=type("AuthenticationError", (Exception,), {}),
            NotFoundError=type("NotFoundError", (Exception,), {}),
            RateLimitError=type("RateLimitError", (Exception,), {}),
            BadRequestError=type("BadRequestError", (Exception,), {}),
        )

    def test_isinstance_auth(self):
        lm = self._litellm()
        self.assertEqual(classify_llm_error(lm.AuthenticationError("x"), litellm_module=lm), "auth")

    def test_isinstance_bad_request(self):
        lm = self._litellm()
        self.assertEqual(classify_llm_error(lm.BadRequestError("x"), litellm_module=lm), "bad_request")


class TestIsPermanent(unittest.TestCase):
    def test_permanent_kinds(self):
        for kind in ("auth", "not_found", "bad_request"):
            self.assertTrue(is_permanent(kind), kind)

    def test_non_permanent_kinds(self):
        for kind in ("rate_limited", "transient", "context_length"):
            self.assertFalse(is_permanent(kind), kind)


if __name__ == "__main__":
    unittest.main()
