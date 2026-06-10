"""Classification jobs (analyser, diagnostic, arbiter) pin
``temperature=0`` regardless of the model's profile setting.

Without it, non-deterministic decoding produces slightly different
findings each run: validated analysis findings can silently re-appear
on a Re-Analyse click, and arbiter decisions (queue / interrupt) can
flicker across runs.

These three call sites are classification, not generation: there is a
target answer, not a creative output. The coordinator stays
profile-driven (it composes user-facing replies, where some variance
is appropriate).

Contract being pinned:
  - analyser ``llm_completion`` always sends temperature=0 (unless
    the profile explicitly says ``temperature: null`` for the model)
  - arbiter LLMClient always has temperature=0 (same caveat)
  - diagnostic LLMClient always has temperature=0 (same caveat)
  - coordinator LLMClient keeps the profile temperature
  - ``drop_params=True`` already on each call site, so providers that
    reject the param strip it server-side
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PASSIVE_ANALYSIS_DIR = Path(__file__).resolve().parent.parent.parent / "passive-analyser"
if str(_PASSIVE_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_PASSIVE_ANALYSIS_DIR))


# ---------------------------------------------------------------------------
# Analyser side: passive-analyser/llm_layer.py
# ---------------------------------------------------------------------------


class TestAnalyserTemperatureOverride(unittest.TestCase):
    """``_resolve_model_tuning`` must force temperature=0 even when
    the model profile specifies a different value."""

    def setUp(self) -> None:
        # Reset the per-process tuning cache so each test sees a fresh
        # resolution. The cache is keyed by model name.
        from analyser import llm_layer
        llm_layer._tuning_cache.clear()

    def test_overrides_non_zero_profile_temperature_with_zero(self):
        """A profile with temperature=0.4 (e.g. coordinator default)
        must NOT carry over into the analyser. The analyser is
        classification — pin to 0."""
        from analyser import llm_layer
        from app.llm.model_profiles import ModelProfile, ResolvedProfile

        non_zero_profile = ResolvedProfile(
            model_profile=ModelProfile(),
            temperature=0.4,
            top_p=None,
            generation_params={},
            matched_pattern="some/.*",
        )
        with patch.object(llm_layer, "get_model_profile", return_value=non_zero_profile), \
             patch.object(llm_layer, "load_yaml_profiles_with_override", return_value={}):
            tuning = llm_layer._resolve_model_tuning("anthropic/claude-sonnet-4-6")

        self.assertEqual(tuning.get("temperature"), 0.0)

    def test_preserves_null_profile_temperature(self):
        """Profile with ``temperature: null`` (model deprecated the
        param) must omit temperature entirely. drop_params handles
        this server-side too, but being explicit avoids confusing
        diagnostic logs."""
        from analyser import llm_layer
        from app.llm.model_profiles import ModelProfile, ResolvedProfile

        null_temp_profile = ResolvedProfile(
            model_profile=ModelProfile(),
            temperature=None,
            top_p=None,
            generation_params={},
            matched_pattern="anthropic/claude-opus-4-7",
        )
        with patch.object(llm_layer, "get_model_profile", return_value=null_temp_profile), \
             patch.object(llm_layer, "load_yaml_profiles_with_override", return_value={}):
            tuning = llm_layer._resolve_model_tuning("anthropic/claude-opus-4-7")

        self.assertNotIn("temperature", tuning)

    def test_locked_temperature_is_preserved(self):
        """Some models (GPT-5 family) reject any temperature other than
        1.0. ``temperature_lock: true`` in the profile signals that —
        the override must NOT apply or the model returns an error."""
        from analyser import llm_layer
        from app.llm.model_profiles import ModelProfile, ResolvedProfile

        locked_profile = ResolvedProfile(
            model_profile=ModelProfile(),
            temperature=1.0,
            top_p=None,
            generation_params={},
            matched_pattern="gpt-5",
            temperature_lock=True,
        )
        with patch.object(llm_layer, "get_model_profile", return_value=locked_profile), \
             patch.object(llm_layer, "load_yaml_profiles_with_override", return_value={}):
            tuning = llm_layer._resolve_model_tuning("openai/gpt-5")

        self.assertEqual(tuning.get("temperature"), 1.0)

    def test_profile_top_p_and_generation_params_still_carry_through(self):
        """Only temperature is overridden — top_p and other
        generation params from the profile must survive (e.g.
        ``think: false`` for Ollama models)."""
        from analyser import llm_layer
        from app.llm.model_profiles import ModelProfile, ResolvedProfile

        profile_with_extras = ResolvedProfile(
            model_profile=ModelProfile(),
            temperature=0.7,
            top_p=0.9,
            generation_params={"think": False},
            matched_pattern="ollama_chat/.*",
        )
        with patch.object(llm_layer, "get_model_profile", return_value=profile_with_extras), \
             patch.object(llm_layer, "load_yaml_profiles_with_override", return_value={}):
            tuning = llm_layer._resolve_model_tuning("ollama_chat/gemma4:26b")

        self.assertEqual(tuning.get("temperature"), 0.0)
        self.assertEqual(tuning.get("top_p"), 0.9)
        self.assertEqual(tuning.get("think"), False)


# ---------------------------------------------------------------------------
# Agent side: arbiter and diagnostic LLMClients
# ---------------------------------------------------------------------------


def _init_runtime_with_profile_temperature(profile_temperature, *, lock=False):
    """Spin up a RuntimeState with skills/Roon mocked, model_profiles
    patched so the resolved profile carries the given temperature."""
    from app.llm.model_profiles import ModelProfile, ResolvedProfile
    from app.runtime.state import RuntimeState

    profile = ResolvedProfile(
        model_profile=ModelProfile(),
        temperature=profile_temperature,
        top_p=None,
        generation_params={},
        matched_pattern="dummy/.*",
        temperature_lock=lock,
    )
    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
        # Arbiter + diagnostic explicitly mirror LLM_MODEL — exercises
        # the "shared client" branch of init where clients of the same
        # model could otherwise alias the coordinator's.
        "LLM_MODEL_ARBITER": "dummy/dummy-model",
        "LLM_MODEL_DIAGNOSTIC": "dummy/dummy-model",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", MagicMock),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("", ""),
        ),
        patch("app.runtime.state.get_model_profile", return_value=profile),
        patch("app.runtime.state.load_yaml_profiles_with_override", return_value={}),
    ):
        rs = RuntimeState()
        rs.ensure_initialised()
    return rs


class TestArbiterClientTemperature(unittest.TestCase):

    def test_arbiter_pins_temperature_zero_overriding_profile(self):
        """Even when the resolved profile says temperature=0.4, the
        arbiter client must be temperature=0.0 — arbitration is
        classification, no creative variance wanted."""
        rs = _init_runtime_with_profile_temperature(0.4)

        self.assertEqual(rs.arbiter_client.temperature, 0.0)

    def test_arbiter_separate_client_from_coordinator(self):
        """When the arbiter model matches the coordinator's, the arbiter
        must still have its own client — the temperature override would
        otherwise mutate the coordinator's temperature."""
        rs = _init_runtime_with_profile_temperature(0.4)

        self.assertIsNot(rs.arbiter_client, rs.llm_client)

    def test_arbiter_respects_null_temperature_in_profile(self):
        """For models that deprecated the temperature param, the
        arbiter client must omit it (LLMClient.temperature=None)."""
        rs = _init_runtime_with_profile_temperature(None)

        self.assertIsNone(rs.arbiter_client.temperature)

    def test_arbiter_respects_locked_temperature(self):
        """When the profile locks temperature (e.g. GPT-5 requires
        1.0), the arbiter client must keep that — overriding to 0
        would make every call fail."""
        rs = _init_runtime_with_profile_temperature(1.0, lock=True)

        self.assertEqual(rs.arbiter_client.temperature, 1.0)


class TestDiagnosticClientTemperature(unittest.TestCase):

    def test_diagnostic_pins_temperature_zero_overriding_profile(self):
        rs = _init_runtime_with_profile_temperature(0.4)

        self.assertEqual(rs.diagnostic_client.temperature, 0.0)


class TestCoordinatorClientUnchanged(unittest.TestCase):
    """The coordinator does composition + tool selection, not
    classification. Its temperature stays profile-driven so existing
    user-facing behaviour is preserved."""

    def test_coordinator_keeps_profile_temperature(self):
        rs = _init_runtime_with_profile_temperature(0.4)

        self.assertEqual(rs.llm_client.temperature, 0.4)

if __name__ == "__main__":
    unittest.main()
