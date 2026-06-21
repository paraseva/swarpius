"""Tests for required-config detection.

The Settings UI's routing logic depends on these helpers — when
`required_config_complete()` is False, the frontend renders the
Settings page with the missing fields highlighted instead of the
chat UI.
"""

import os
import unittest
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.settings import (
    required_config_complete,
    required_config_missing,
    reset_settings_for_tests,
)
from app.settings.endpoints import config_pristine


class TestRequiredConfigMissing(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_LLM_MODEL_when_no_model_set(self):
        self.assertEqual(required_config_missing(), ["LLM_MODEL"])

    @patch.dict(os.environ, {"LLM_MODEL": "model-without-provider"}, clear=True)
    def test_returns_LLM_MODEL_when_no_provider_prefix(self):
        """LiteLLM requires provider/model format; reject anything else."""
        self.assertEqual(required_config_missing(), ["LLM_MODEL"])

    @patch.dict(os.environ, {"LLM_MODEL": "/just-a-slash"}, clear=True)
    def test_returns_LLM_MODEL_when_empty_provider(self):
        self.assertEqual(required_config_missing(), ["LLM_MODEL"])

    @patch.dict(os.environ, {"LLM_MODEL": "anthropic/claude-sonnet-4-6"}, clear=True)
    def test_returns_LLM_API_KEY_when_model_set_but_no_key(self):
        self.assertEqual(
            required_config_missing(),
            ["LLM_API_KEY_ANTHROPIC"],
        )

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-sonnet-4-6",
            "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
        },
        clear=True,
    )
    def test_returns_empty_when_model_and_key_set(self):
        self.assertEqual(required_config_missing(), [])

    @patch.dict(os.environ, {"LLM_MODEL": "ollama/llama3"}, clear=True)
    def test_local_provider_ollama_needs_no_key(self):
        self.assertEqual(required_config_missing(), [])

    @patch.dict(os.environ, {"LLM_MODEL": "ollama_chat/gemma3:27b"}, clear=True)
    def test_local_provider_ollama_chat_needs_no_key(self):
        self.assertEqual(required_config_missing(), [])

    @patch.dict(os.environ, {"LLM_MODEL": "OLLAMA/llama3"}, clear=True)
    def test_local_provider_match_is_case_insensitive(self):
        """``OLLAMA/llama3`` should be recognised as local; the key
        comparison normalises to lowercase."""
        self.assertEqual(required_config_missing(), [])

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
            "LLM_MODEL_ARBITER": "openai/gpt-5",
            "ENABLE_INTERRUPT_ARBITER": "true",
        },
        clear=True,
    )
    def test_arbiter_override_provider_key_required_when_enabled(self):
        """Per-agent override that points at a different provider
        needs that provider's key too — but only when the agent is
        enabled. Optional agents default off."""
        self.assertEqual(
            required_config_missing(),
            ["LLM_API_KEY_OPENAI"],
        )

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
            "LLM_MODEL_ARBITER": "openai/gpt-5",
        },
        clear=True,
    )
    def test_arbiter_override_provider_key_skipped_when_disabled(self):
        """Disabled arbiter never constructs an LLM client, so its
        override provider key isn't required."""
        self.assertEqual(required_config_missing(), [])

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
            "LLM_MODEL_DIAGNOSTIC": "gemini/gemini-2.5-pro",
            "ENABLE_DIAGNOSTIC_AGENT": "true",
        },
        clear=True,
    )
    def test_diagnostic_override_provider_key_required_when_enabled(self):
        self.assertEqual(
            required_config_missing(),
            ["LLM_API_KEY_GEMINI"],
        )

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
            "LLM_MODEL_ANALYSER": "openai/gpt-5",
            "ENABLE_PASSIVE_ANALYSER": "true",
        },
        clear=True,
    )
    def test_analyser_override_provider_key_required_when_enabled(self):
        """An enabled analyser with a provider-override demands that
        provider's key, same as the other optional agents."""
        self.assertEqual(
            required_config_missing(),
            ["LLM_API_KEY_OPENAI"],
        )

    @patch.dict(os.environ, {"LLM_MODEL": "google/gemini-2.5-pro"}, clear=True)
    def test_provider_key_name_uses_upper_case(self):
        """The conventional env var name is ``LLM_API_KEY_<PROVIDER>``
        in uppercase regardless of how the provider appears in the
        model string."""
        self.assertEqual(
            required_config_missing(),
            ["LLM_API_KEY_GOOGLE"],
        )


class TestRequiredConfigComplete(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    @patch.dict(os.environ, {}, clear=True)
    def test_false_when_missing(self):
        self.assertFalse(required_config_complete())

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
        },
        clear=True,
    )
    def test_true_when_complete(self):
        self.assertTrue(required_config_complete())


class TestConfigPristine(unittest.TestCase):
    """``config_pristine`` drives the first-run Getting Started intro:
    True only while the user has set no assistant-configuration value
    (any field on any Settings page). Operational overrides — data dir,
    log file, ports — don't count, and the flag never flips back once
    anything is set."""

    def _pristine(self, managed: dict) -> bool:
        with patch(
            "app.settings.endpoints.read_managed_env", return_value=managed,
        ):
            return config_pristine()

    def test_true_when_nothing_set(self):
        self.assertTrue(self._pristine({}))

    def test_false_when_llm_model_set(self):
        self.assertFalse(self._pristine({"LLM_MODEL": "anthropic/claude-x"}))

    def test_false_when_api_key_set(self):
        self.assertFalse(self._pristine({"LLM_API_KEY_ANTHROPIC": "sk-ant-abc"}))

    def test_false_when_non_llm_page_field_set(self):
        """A Roon-tab field counts the same as the LLM fields."""
        self.assertFalse(self._pristine({"ROON_CORE_URL": "http://10.0.0.1:9330"}))

    def test_default_roon_zone_no_longer_a_recognised_config_var(self):
        """DEFAULT_ROON_ZONE was removed — the default zone is chosen at
        runtime and persisted, not configured via env. Setting it must not
        count as configuring the assistant."""
        self.assertTrue(self._pristine({"DEFAULT_ROON_ZONE": "Kitchen"}))

    def test_true_when_only_operational_vars_set(self):
        """Relocating the data dir or log file isn't configuring the
        assistant — the intro should still show."""
        self.assertTrue(self._pristine({
            "SWARPIUS_DATA_DIR": "/mnt/usb/swarpius",
            "LOG_FILE": "/var/log/swarpius.log",
        }))

    def test_true_when_managed_value_is_blank(self):
        """An empty assignment is 'not set'."""
        self.assertTrue(self._pristine({"LLM_MODEL": "   "}))


if __name__ == "__main__":
    unittest.main()
