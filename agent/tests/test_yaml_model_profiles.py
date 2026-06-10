"""Tests for YAML-based model profile configuration."""

import tempfile
import textwrap
import unittest
from pathlib import Path

try:
    from tests.stub_modules import install_common_test_stubs
except ImportError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.data_paths import AGENT_ROOT  # noqa: E402
from app.llm.model_profiles import (  # noqa: E402
    get_model_profile,
    load_yaml_profiles,
    load_yaml_profiles_with_override,
)


def _write_config(tmp: str, content: str) -> Path:
    p = Path(tmp) / "model_profiles.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _write_named(dir_: Path, name: str, content: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


class TestLoadYamlProfiles(unittest.TestCase):
    """YAML config loading and structure validation."""

    def test_load_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(tmp, """\
                defaults:
                  temperature: 0.5
                profiles:
                  - pattern: "ollama.*gemma4"
                    temperature: 0.3
                    top_p: 0.9
                    generation_params:
                      think: false
            """)
            config = load_yaml_profiles(path)
            self.assertEqual(config["defaults"]["temperature"], 0.5)
            self.assertEqual(len(config["profiles"]), 1)
            self.assertEqual(config["profiles"][0]["temperature"], 0.3)

    def test_missing_file_returns_empty_defaults(self):
        config = load_yaml_profiles(Path("/nonexistent/model_profiles.yaml"))
        self.assertEqual(config["defaults"], {})
        self.assertEqual(config["profiles"], [])

    def test_empty_file_returns_empty_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model_profiles.yaml"
            path.write_text("", encoding="utf-8")
            config = load_yaml_profiles(path)
            self.assertEqual(config["defaults"], {})
            self.assertEqual(config["profiles"], [])


class TestProfileMatching(unittest.TestCase):
    """Profile pattern matching against full model string."""

    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._path = _write_config(tmp, """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "ollama.*gemma4"
                    temperature: 0.3
                    top_p: 0.9
                    generation_params:
                      think: false
                  - pattern: "gemma4"
                    temperature: 0.3
                    top_p: 0.9
                  - pattern: "ollama"
                    generation_params:
                      think: false
                  - pattern: "anthropic/"
                    temperature: 0.0
            """)
            self._config = load_yaml_profiles(self._path)

    def test_specific_provider_model_match(self):
        """ollama_chat/gemma4:26b should match 'ollama.*gemma4' first."""
        profile = get_model_profile(
            "ollama_chat/gemma4:26b", yaml_config=self._config,
        )
        # Should get temperature from the specific profile
        self.assertEqual(profile.temperature, 0.3)
        self.assertEqual(profile.top_p, 0.9)
        self.assertEqual(profile.generation_params, {"think": False})

    def test_model_only_match(self):
        """hosted_vllm/gemma4:26b should match 'gemma4' (not 'ollama')."""
        profile = get_model_profile(
            "hosted_vllm/gemma4:26b", yaml_config=self._config,
        )
        self.assertEqual(profile.temperature, 0.3)
        self.assertEqual(profile.top_p, 0.9)
        self.assertEqual(profile.generation_params, {})

    def test_provider_only_match(self):
        """ollama/mistral:7b should match 'ollama' catch-all."""
        profile = get_model_profile(
            "ollama/mistral:7b", yaml_config=self._config,
        )
        self.assertEqual(profile.temperature, 0.0)  # from defaults
        self.assertEqual(profile.generation_params, {"think": False})

    def test_no_match_uses_defaults(self):
        """openai/gpt-4o should not match any profile."""
        profile = get_model_profile(
            "openai/gpt-4o", yaml_config=self._config,
        )
        self.assertEqual(profile.temperature, 0.0)
        self.assertIsNone(profile.top_p)
        self.assertEqual(profile.generation_params, {})

    def test_anthropic_match(self):
        """anthropic/claude-sonnet-4-6 should match 'anthropic/'."""
        profile = get_model_profile(
            "anthropic/claude-sonnet-4-6", yaml_config=self._config,
        )
        self.assertEqual(profile.temperature, 0.0)


class TestLoopKnobsFromProfile(unittest.TestCase):
    """Coordinator loop knobs configurable via YAML profiles."""

    def test_defaults_when_no_profile(self):
        """Without a matching profile, dataclass defaults apply."""
        config = load_yaml_profiles(Path("/nonexistent"))
        profile = get_model_profile(
            "some_provider/unknown-model-xyz", yaml_config=config,
        )
        self.assertEqual(profile.model_profile.max_coordinator_steps, 12)
        self.assertEqual(profile.model_profile.soft_nudge_step, 8)

    def test_knobs_from_yaml_profile(self):
        """max_coordinator_steps and soft_nudge_step loaded from YAML."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(tmp, """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "gemma4"
                    max_coordinator_steps: 20
                    soft_nudge_step: 14
            """)
            config = load_yaml_profiles(path)
            profile = get_model_profile(
                "ollama_chat/gemma4:26b", yaml_config=config,
            )
            self.assertEqual(profile.model_profile.max_coordinator_steps, 20)
            self.assertEqual(profile.model_profile.soft_nudge_step, 14)

    def test_partial_override_uses_defaults_for_rest(self):
        """Specifying only one knob leaves the other at its default."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(tmp, """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "gemma4"
                    max_coordinator_steps: 16
            """)
            config = load_yaml_profiles(path)
            profile = get_model_profile(
                "ollama_chat/gemma4:26b", yaml_config=config,
            )
            self.assertEqual(profile.model_profile.max_coordinator_steps, 16)
            self.assertEqual(profile.model_profile.soft_nudge_step, 8)


class TestLLMClientKwargs(unittest.TestCase):
    """Profile params actually reach the LiteLLM call."""

    def _make_mock_response(self):
        from unittest.mock import MagicMock
        mock_message = MagicMock()
        mock_message.content = "test response"
        mock_message.tool_calls = None
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None
        return mock_response

    def _run_completion_capturing_kwargs(self, client):
        """Run a completion call and return the kwargs passed to litellm."""
        import asyncio
        import sys
        from unittest.mock import MagicMock

        mock_response = self._make_mock_response()
        captured_kwargs = {}

        # Create a mock litellm module with acompletion
        mock_litellm = MagicMock()

        async def capture_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_response

        mock_litellm.acompletion = capture_acompletion
        mock_litellm.success_callback = []
        mock_litellm.failure_callback = []

        # Patch the lazy import inside completion()
        original = sys.modules.get("litellm")
        sys.modules["litellm"] = mock_litellm
        try:
            asyncio.run(client.completion(
                messages=[{"role": "user", "content": "hello"}],
            ))
        finally:
            if original is not None:
                sys.modules["litellm"] = original
            else:
                sys.modules.pop("litellm", None)

        return captured_kwargs

    def test_profile_params_passed_to_litellm(self):
        """Verify temperature, top_p, and generation_params end up in acompletion kwargs."""
        from app.llm.client import LLMClient

        client = LLMClient(
            model="ollama_chat/gemma4:26b",
            api_key="test",
            temperature=0.3,
            top_p=0.9,
            generation_params={"think": False},
        )
        kwargs = self._run_completion_capturing_kwargs(client)

        self.assertEqual(kwargs["temperature"], 0.3)
        self.assertEqual(kwargs["top_p"], 0.9)
        self.assertFalse(kwargs["think"])

    def test_default_client_no_extra_params(self):
        """Client with no profile params doesn't inject top_p or generation_params."""
        from app.llm.client import LLMClient

        client = LLMClient(model="anthropic/claude-sonnet-4-6", api_key="test")
        kwargs = self._run_completion_capturing_kwargs(client)

        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertNotIn("top_p", kwargs)
        self.assertNotIn("think", kwargs)

    def test_null_temperature_omitted_from_kwargs(self):
        """A null profile temperature is not sent to LiteLLM.

        Needed for models that deprecate the param entirely (e.g. claude-opus-4-7).
        """
        from app.llm.client import LLMClient

        client = LLMClient(
            model="anthropic/claude-opus-4-7",
            api_key="test",
            temperature=None,
        )
        kwargs = self._run_completion_capturing_kwargs(client)

        self.assertNotIn("temperature", kwargs)


class TestNullTemperatureInProfile(unittest.TestCase):
    """`temperature: null` in YAML resolves to None and is preserved through get_model_profile."""

    def test_profile_null_temperature_overrides_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(tmp, """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "claude.*opus-4-7"
                    temperature: null
            """)
            config = load_yaml_profiles(path)
            resolved = get_model_profile(
                "anthropic/claude-opus-4-7", yaml_config=config,
            )
            self.assertIsNone(resolved.temperature)

    def test_end_to_end_yaml_to_client(self):
        """Full pipeline: YAML config → ResolvedProfile → LLMClient params."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(tmp, """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "ollama.*gemma4"
                    temperature: 0.3
                    top_p: 0.9
                    generation_params:
                      think: false
            """)
            config = load_yaml_profiles(path)
            resolved = get_model_profile(
                "ollama_chat/gemma4:26b", yaml_config=config,
            )

            from app.llm.client import LLMClient
            client = LLMClient(
                model="ollama_chat/gemma4:26b",
                api_key="test",
                temperature=resolved.temperature,
                top_p=resolved.top_p,
                generation_params=resolved.generation_params,
            )

            self.assertEqual(client.temperature, 0.3)
            self.assertEqual(client.top_p, 0.9)
            self.assertEqual(client.generation_params, {"think": False})


class TestProfileOverride(unittest.TestCase):
    """A user override file in the data dir layers over bundled defaults.

    Lets bundle users patch a model's tuning (e.g. a future deprecated
    param) without editing the read-only bundled config, which an upgrade
    would replace.
    """

    def test_missing_override_returns_base_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _write_named(Path(tmp), "model_profiles.yaml", """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "anthropic/"
                    temperature: 0.2
            """)
            merged = load_yaml_profiles_with_override(
                base, Path(tmp) / "does-not-exist.yaml",
            )
            self.assertEqual(merged, load_yaml_profiles(base))

    def test_override_profile_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            base = _write_named(d, "model_profiles.yaml", """\
                defaults:
                  temperature: 0.0
                profiles:
                  - pattern: "claude.*opus"
                    temperature: null
            """)
            override = _write_named(d / "override", "model_profiles.yaml", """\
                profiles:
                  - pattern: "claude.*opus"
                    temperature: 0.5
            """)
            merged = load_yaml_profiles_with_override(base, override)
            resolved = get_model_profile(
                "anthropic/claude-opus-4-8", yaml_config=merged,
            )
            # User entry is matched before the bundled one.
            self.assertEqual(resolved.temperature, 0.5)

    def test_override_defaults_merge_over_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            base = _write_named(d, "model_profiles.yaml", """\
                defaults:
                  temperature: 0.0
                  top_p: 0.1
                profiles: []
            """)
            override = _write_named(d / "override", "model_profiles.yaml", """\
                defaults:
                  temperature: 0.9
            """)
            merged = load_yaml_profiles_with_override(base, override)
            # Overridden key wins; base-only key survives.
            self.assertEqual(merged["defaults"]["temperature"], 0.9)
            self.assertEqual(merged["defaults"]["top_p"], 0.1)


class TestShippedOpusTemperature(unittest.TestCase):
    """The included profile omits `temperature` for Opus 4.7+ (which rejects
    it) and keeps it for 4.6 and earlier (which accept it)."""

    def test_temperature_omitted_only_for_opus_4_7_plus(self):
        config = load_yaml_profiles(AGENT_ROOT / "model_profiles.yaml")

        def temperature(model: str):
            return get_model_profile(model, yaml_config=config).temperature

        self.assertIsNone(temperature("anthropic/claude-opus-4-8"))
        self.assertIsNotNone(temperature("anthropic/claude-opus-4-6"))


