"""Tests for RuntimeState's LLM init + prompt builders:
``_resolve_agent_model``, ``_parse_model_spec``,
``build_coordinator_system_prompt``, ``build_arbiter_system_prompt``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.runtime.state import RuntimeState


class TestResolveAgentModel(unittest.TestCase):
    def test_env_value_wins(self):
        with patch.dict("os.environ", {"LLM_MODEL_DIAGNOSTIC": "anthropic/x"}):
            self.assertEqual(
                RuntimeState._resolve_agent_model("LLM_MODEL_DIAGNOSTIC", default="fallback"),
                "anthropic/x",
            )

    def test_blank_env_falls_back_to_default(self):
        with patch.dict("os.environ", {"LLM_MODEL_DIAGNOSTIC": "   "}):
            self.assertEqual(
                RuntimeState._resolve_agent_model("LLM_MODEL_DIAGNOSTIC", default="fallback"),
                "fallback",
            )

    def test_missing_env_falls_back_to_default(self):
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("LLM_MODEL_DIAGNOSTIC", None)
            self.assertEqual(
                RuntimeState._resolve_agent_model("LLM_MODEL_DIAGNOSTIC", default="fallback"),
                "fallback",
            )

    def test_whitespace_trimmed_from_env(self):
        with patch.dict("os.environ", {"LLM_MODEL_DIAGNOSTIC": "  anthropic/x  "}):
            self.assertEqual(
                RuntimeState._resolve_agent_model("LLM_MODEL_DIAGNOSTIC"),
                "anthropic/x",
            )

    def test_unknown_env_key_raises(self):
        """Only the four agent-model env keys are accepted; arbitrary
        keys must raise so a typo doesn't silently fall through to the
        default."""
        with self.assertRaises(ValueError):
            RuntimeState._resolve_agent_model("FOO_MODEL", default="fallback")


class TestParseModelSpec(unittest.TestCase):
    def test_spec_without_slash_raises(self):
        with self.assertRaises(ValueError) as ctx:
            RuntimeState._parse_model_spec("just-a-model-name")
        self.assertIn("provider/model format", str(ctx.exception))

    def test_provider_model_returns_spec_and_api_key(self):
        with patch.dict("os.environ", {"LLM_API_KEY_ANTHROPIC": "sk-abc"}):
            spec, api_key = RuntimeState._parse_model_spec("anthropic/claude-sonnet-4-6")
        self.assertEqual(spec, "anthropic/claude-sonnet-4-6")
        self.assertEqual(api_key, "sk-abc")

    def test_provider_uppercased_for_env_key(self):
        with patch.dict("os.environ", {"LLM_API_KEY_OPENAI": "sk-def"}):
            _, api_key = RuntimeState._parse_model_spec("openai/gpt-4")
        self.assertEqual(api_key, "sk-def")

    def test_missing_api_key_returns_empty_string(self):
        """Local providers (Ollama) have no API key; empty string is expected."""
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("LLM_API_KEY_OLLAMA_CHAT", None)
            _os.environ.pop("LLM_API_KEY_OLLAMA", None)
            _, api_key = RuntimeState._parse_model_spec("ollama_chat/gemma3")
        self.assertEqual(api_key, "")

    def test_api_key_whitespace_trimmed(self):
        with patch.dict("os.environ", {"LLM_API_KEY_ANTHROPIC": "  sk-abc  "}):
            _, api_key = RuntimeState._parse_model_spec("anthropic/claude")
        self.assertEqual(api_key, "sk-abc")


class TestBuildCoordinatorSystemPrompt(unittest.TestCase):
    def test_persona_inserted_when_set(self):
        with patch.dict("os.environ", {"LLM_PERSONA": "cheerful British butler"}):
            prompt = RuntimeState.build_coordinator_system_prompt()
        self.assertIn("cheerful British butler", prompt)
        self.assertIn("Adopt the following persona", prompt)

    def test_persona_absent_when_unset(self):
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("LLM_PERSONA", None)
            prompt = RuntimeState.build_coordinator_system_prompt()
        self.assertNotIn("Adopt the following persona", prompt)

    def test_persona_absent_when_blank(self):
        with patch.dict("os.environ", {"LLM_PERSONA": "   "}):
            prompt = RuntimeState.build_coordinator_system_prompt()
        self.assertNotIn("Adopt the following persona", prompt)

    def test_locked_at_first_access(self):
        """Persona resolves through ``Settings``, which is locked at first
        access. The autouse settings-reset fixture in conftest.py gives
        each test a clean slate; within a single test, env mutations
        after the first ``build_coordinator_system_prompt()`` call do
        not change the result. (Production code relies on the same
        invariant — see app.settings.)"""
        with patch.dict("os.environ", {"LLM_PERSONA": "stoic"}):
            first = RuntimeState.build_coordinator_system_prompt()
        # Mutate env after first access; settings is already cached, so
        # the second call must agree with the first.
        with patch.dict("os.environ", {"LLM_PERSONA": "different"}):
            second = RuntimeState.build_coordinator_system_prompt()
        self.assertIn("stoic", first)
        self.assertIn("stoic", second)
        self.assertNotIn("different", second)


if __name__ == "__main__":
    unittest.main()
