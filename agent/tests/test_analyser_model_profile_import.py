"""Tests for the analyser's model-profile integration.

The analyser imports app.model_profiles via a sys.path shim in
llm_layer.py. The integration must surface failures rather than
silently falling back — a swallowed import error would leave
_resolve_model_tuning returning {"temperature": 0.0} with no signal
to operators.

These tests pin the two surfaces:

  1. In the normal repo layout the import succeeds and a known model
     resolves to a real profile (not the fallback).
  2. If the profile system is genuinely unavailable, the fallback path
     logs at WARNING level (not INFO) so operators see the degradation.
"""

import logging
import unittest
from unittest.mock import patch

# ``passive-analyser/`` is on sys.path via tests/conftest.py, so this
# import succeeds at module load and stays consistent across tests.
from analyser import llm_layer


class TestAnalyserProfileImport(unittest.TestCase):
    """Each test starts from a clean ``_tuning_cache`` so prior test
    lookups don't shadow this one. The cache is module-level state
    inside ``llm_layer`` (used as an LRU for resolved profiles); we
    don't reload the module between tests because importlib.reload
    leaves stale references in ``sys.modules`` if a test bails before
    its cleanup runs."""

    def setUp(self) -> None:
        llm_layer._tuning_cache.clear()

    def test_import_path_resolves_model_profile(self) -> None:
        """llm_layer's sys.path shim must find app.model_profiles
        in the standard repo layout. Docker was the known breakage site.
        """
        self.assertIsNotNone(
            llm_layer.get_model_profile,
            "llm_layer failed to import app.llm.model_profiles — check "
            "that agent/app/ is accessible (sys.path, bind mounts, "
            "or COPY in Dockerfile).",
        )
        self.assertIsNotNone(
            llm_layer.load_yaml_profiles_with_override,
            "llm_layer failed to import load_yaml_profiles_with_override",
        )

    def test_resolve_model_tuning_returns_profile_value_for_gpt5(self) -> None:
        """With the profile system available and the yaml present, a model
        whose profile sets a locked temperature returns that value —
        not the analyser's standard temperature=0 override. Picks gpt-5
        because the yaml marks its temperature locked at 1.0 (GPT-5
        rejects any other value). If the Docker image lost access to
        model_profiles.yaml, the canary would resolve via the fallback
        path instead — the assertion would still pin a real lookup did
        happen because no fallback emits ``max_coordinator_steps``.
        """
        tuning = llm_layer._resolve_model_tuning("openai/gpt-5")

        self.assertEqual(
            tuning.get("temperature"), 1.0,
            f"Expected the locked temperature=1.0 from the gpt-5 profile; got {tuning}. "
            "If the value is 0.0, either the profile system didn't see model_profiles.yaml "
            "(Docker bind-mount / sys.path shim broken) or the temperature_lock flag is "
            "missing from the gpt-5 entry.",
        )

    def test_fallback_path_logs_at_warning_level(self) -> None:
        """If the profile system is unavailable, the fallback must be
        loud enough to catch. INFO-level was invisible in production;
        WARNING shows up in default log configs.
        """
        with (
            patch.object(llm_layer, "get_model_profile", None),
            patch.object(llm_layer, "load_yaml_profiles_with_override", None),
            self.assertLogs("analyse.llm", level=logging.WARNING) as cap,
        ):
            tuning = llm_layer._resolve_model_tuning("anthropic/claude-opus-4-6")

        self.assertEqual(tuning, {"temperature": 0.0})
        self.assertTrue(
            any("profile system unavailable" in m.lower() for m in cap.output),
            f"Expected a 'profile system unavailable' warning; got {cap.output}",
        )


if __name__ == "__main__":
    unittest.main()
