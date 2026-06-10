"""Tests for the boot-time / on-demand LLM config validator.

The validator's job is to confirm that every enabled agent's
(provider, model, api_key) tuple works against the live provider —
API key valid, model present, provider reachable. Tuples are
deduplicated so two agents sharing a spec are tested once; HTTP calls
fan out in parallel via ``asyncio.gather``.

These tests mock the network layer at the ``requests.get`` boundary
so the per-provider checkers (including HTTP status / payload
mapping logic) run on the call path — that's where defects hide.
"""

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

import requests

from app.settings import reset_settings_for_tests
from app.settings import validation as config_validation
from app.settings.validation import (
    AgentResult,
    ConfigValidator,
    ValidationState,
    _split_model,
)


def _fake_response(status: int, payload: object) -> MagicMock:
    """Build a stand-in for a ``requests.Response`` that satisfies the
    surface area the checkers touch (``status_code`` + ``json()``)."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def _run(coro):
    return asyncio.run(coro)


class TestSplitModel(unittest.TestCase):
    def test_splits_and_lowercases_provider(self):
        self.assertEqual(_split_model("Anthropic/claude-X"), ("anthropic", "claude-X"))

    def test_returns_empty_on_missing_slash(self):
        self.assertEqual(_split_model("just-a-model"), ("", ""))

    def test_returns_empty_on_none(self):
        self.assertEqual(_split_model(None), ("", ""))

    def test_returns_empty_on_empty_string(self):
        self.assertEqual(_split_model(""), ("", ""))


class TestCheckTuple(unittest.TestCase):
    """Per-provider checker behaviour."""

    def test_anthropic_alias_matches_dated_snapshot(self):
        """Anthropic's /v1/models lists versioned IDs but the messages
        API accepts the un-dated alias. A user typing
        ``claude-haiku-4-5`` should validate against the listed
        ``claude-haiku-4-5-20251001``."""
        resp = _fake_response(
            200, {"data": [{"id": "claude-haiku-4-5-20251001"}]},
        )
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("anthropic", "claude-haiku-4-5", "sk-ant"),
            )
        self.assertTrue(result["ok"])

    def test_anthropic_alias_match_requires_date_suffix(self):
        """The alias rule must not accidentally match unrelated
        longer names — only ``-YYYYMMDD`` snapshots count."""
        resp = _fake_response(
            200, {"data": [{"id": "claude-haiku-4-5-extended"}]},
        )
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("anthropic", "claude-haiku-4-5", "sk-ant"),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "not_found")

    def test_anthropic_model_not_found(self):
        resp = _fake_response(200, {"data": [{"id": "other"}]})
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("anthropic", "claude-x", "sk-ant"),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "not_found")

    def test_anthropic_bad_key(self):
        resp = _fake_response(401, {})
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("anthropic", "claude-x", "sk-bad"),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth_failed")

    def test_openai_valid_model(self):
        resp = _fake_response(200, {"data": [{"id": "gpt-5"}]})
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("openai", "gpt-5", "sk-oa"),
            )
        self.assertTrue(result["ok"])

    def test_gemini_strips_models_prefix_before_matching(self):
        """Gemini's response has ``name = "models/<id>"``; the checker
        must strip the prefix or every match would fail."""
        resp = _fake_response(
            200, {"models": [{"name": "models/gemini-1.5-flash"}]},
        )
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("gemini", "gemini-1.5-flash", "AIza"),
            )
        self.assertTrue(result["ok"])

    def test_ollama_accepts_bare_or_latest_suffix(self):
        """Ollama tags often include ``:latest`` — match either form."""
        resp = _fake_response(
            200, {"models": [{"name": "llama3:latest"}]},
        )
        with patch.object(requests, "get", return_value=resp):
            result = config_validation._check_tuple(
                ("ollama", "llama3", ""),
            )
        self.assertTrue(result["ok"])

    def test_timeout_becomes_network_error_kind(self):
        with patch.object(
            requests, "get",
            side_effect=requests.exceptions.Timeout(),
        ):
            result = config_validation._check_tuple(
                ("openai", "gpt-5", "sk-oa"),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "network")

    def test_unknown_provider_with_key_returns_not_validated(self):
        result = config_validation._check_tuple(
            ("deepseek", "deepseek-chat", "sk-ds"),
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_validated"])

    def test_unknown_provider_without_key_fails(self):
        result = config_validation._check_tuple(
            ("deepseek", "deepseek-chat", ""),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth_failed")

    def test_empty_api_key_for_known_provider_is_auth_failed(self):
        result = config_validation._check_tuple(
            ("anthropic", "claude-x", ""),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth_failed")


class TestConfigValidator(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_coordinator_only_passed(self):
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        with patch.object(requests, "get", return_value=resp):
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        results = {r.agent: r for r in status.results}
        self.assertTrue(results["coordinator"].ok)
        self.assertFalse(results["arbiter"].enabled)
        self.assertIsNone(results["arbiter"].ok)
        self.assertEqual(results["arbiter"].detail, "Disabled")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-bad",
        },
        clear=True,
    )
    def test_bad_key_fails_overall(self):
        resp = _fake_response(401, {})
        with patch.object(requests, "get", return_value=resp):
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.FAILED)
        coord = next(r for r in status.results if r.agent == "coordinator")
        self.assertEqual(coord.error_kind, "auth_failed")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "LLM_MODEL_ARBITER": "openai/gpt-5",
            "LLM_API_KEY_OPENAI": "sk-oa",
            "ENABLE_INTERRUPT_ARBITER": "true",
        },
        clear=True,
    )
    def test_two_distinct_providers_both_validated(self):
        """Coordinator and arbiter on different providers: both
        tuples checked, both passed."""
        responses = {
            "https://api.anthropic.com/v1/models":
                _fake_response(200, {"data": [{"id": "claude-x"}]}),
            "https://api.openai.com/v1/models":
                _fake_response(200, {"data": [{"id": "gpt-5"}]}),
        }
        with patch.object(
            requests, "get", side_effect=lambda url, **_: responses[url],
        ):
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        self.assertTrue(all(r.ok for r in status.results if r.enabled))

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "LLM_MODEL_ARBITER": "anthropic/claude-x",
            "ENABLE_INTERRUPT_ARBITER": "true",
        },
        clear=True,
    )
    def test_shared_tuple_dedup_runs_check_once(self):
        """Coordinator and arbiter on the same provider+model+key:
        the HTTP layer is hit once, not twice."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        with patch.object(requests, "get", return_value=resp) as mock_get:
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        self.assertEqual(mock_get.call_count, 1)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "LLM_MODEL_ARBITER": "openai/gpt-5",
        },
        clear=True,
    )
    def test_disabled_optional_agent_is_skipped(self):
        """The arbiter override points at OpenAI but the toggle is
        off — no OpenAI request fires."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        with patch.object(requests, "get", return_value=resp) as mock_get:
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        self.assertEqual(mock_get.call_count, 1)
        arbiter = next(r for r in status.results if r.agent == "arbiter")
        self.assertFalse(arbiter.enabled)
        self.assertIsNone(arbiter.ok)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "ENABLE_INTERRUPT_ARBITER": "true",
        },
        clear=True,
    )
    def test_enabled_agent_without_override_inherits_coordinator(self):
        """Blank optional row + enabled toggle: shares the coordinator's
        tuple. inherits_coordinator flag is set; HTTP fires once."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        with patch.object(requests, "get", return_value=resp) as mock_get:
            status = _run(ConfigValidator().validate())
        self.assertEqual(mock_get.call_count, 1)
        arbiter = next(r for r in status.results if r.agent == "arbiter")
        self.assertTrue(arbiter.enabled)
        self.assertTrue(arbiter.inherits_coordinator)
        self.assertTrue(arbiter.ok)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "LLM_MODEL_ANALYSER": "openai/gpt-5",
            "ENABLE_PASSIVE_ANALYSER": "true",
        },
        clear=True,
    )
    def test_analyser_failure_does_not_gate(self):
        """A non-essential agent (analyser) failing must NOT fail the
        overall validation — only the coordinator gates startup. The
        analyser row still reports its error so the UI/CLI can surface
        the degraded capability."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        with patch.object(requests, "get", return_value=resp):
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        analyser = next(r for r in status.results if r.agent == "analyser")
        self.assertFalse(analyser.ok)
        self.assertEqual(analyser.error_kind, "auth_failed")

    @patch.dict(
        os.environ,
        {"LLM_MODEL": "anthropic/claude-x"},
        clear=True,
    )
    def test_broadcast_callback_invoked_on_transitions(self):
        """Callback fires for VALIDATING then for PASSED/FAILED — the
        UI relies on these intermediate states to render the spinner."""
        emissions = []
        resp = _fake_response(401, {})
        with patch.object(requests, "get", return_value=resp):
            validator = ConfigValidator(broadcast=emissions.append)
            _run(validator.validate())
        states = [e["state"] for e in emissions]
        self.assertIn("validating", states)
        self.assertEqual(states[-1], "failed")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "LLM_MODEL_ARBITER": "openai/gpt-5",
            "LLM_API_KEY_OPENAI": "sk-bad",
            "ENABLE_INTERRUPT_ARBITER": "true",
        },
        clear=True,
    )
    def test_non_coordinator_provider_failure_does_not_gate(self):
        """Coordinator valid, arbiter on a second provider with a bad
        key: the arbiter row fails but overall state stays PASSED —
        sub-agents degrade, they don't gate."""
        responses = {
            "https://api.anthropic.com/v1/models":
                _fake_response(200, {"data": [{"id": "claude-x"}]}),
            "https://api.openai.com/v1/models":
                _fake_response(401, {}),
        }
        with patch.object(
            requests, "get", side_effect=lambda url, **_: responses[url],
        ):
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        arbiter = next(r for r in status.results if r.agent == "arbiter")
        self.assertFalse(arbiter.ok)
        self.assertEqual(arbiter.error_kind, "auth_failed")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "WEB_SEARCH_PROVIDER": "brave",
            "BRAVE_API_KEY": "brave-key",
        },
        clear=True,
    )
    def test_validate_populates_backends_alongside_llm(self):
        """validate() fans backend probes out with the LLM checks; the
        backend result lands in status.backends (not dropped by the
        parallelisation)."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        with patch.object(requests, "get", return_value=resp):
            status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.PASSED)
        backends = {b.backend for b in status.backends}
        self.assertIn("web-search", backends)
        self.assertTrue(all(b.ok for b in status.backends))

    @patch.dict(os.environ, {}, clear=True)
    def test_no_coordinator_model_produces_other_error_row(self):
        """Empty LLM_MODEL: the coordinator row reports a malformed
        spec rather than crashing the validator."""
        status = _run(ConfigValidator().validate())
        self.assertEqual(status.state, ValidationState.FAILED)
        coord = next(r for r in status.results if r.agent == "coordinator")
        self.assertEqual(coord.error_kind, "other")


class TestPendingRestart(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    def test_set_pending_restart_emits(self):
        emissions = []
        validator = ConfigValidator(broadcast=emissions.append)
        validator.set_pending_restart(True)
        self.assertTrue(validator.current().pending_restart)
        self.assertTrue(emissions[-1]["pending_restart"])

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_pending_restart_preserved_across_validate(self):
        """A successful re-validate after Save shouldn't disrupt the
        pending-restart flag — the new state still needs a Restart click
        before it goes live."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        validator = ConfigValidator()
        validator.set_pending_restart(True)
        with patch.object(requests, "get", return_value=resp):
            _run(validator.validate())
        self.assertTrue(validator.current().pending_restart)


class TestMarkProviderFailed(unittest.TestCase):
    """Cross-thread hook called when a live LLM call hits auth /
    not-found. Validator updates matching agent rows and broadcasts
    so the UI surfaces the regression without a Save & Validate."""

    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_emits_via_broadcast(self):
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        emissions = []
        validator = ConfigValidator(broadcast=emissions.append)
        with patch.object(requests, "get", return_value=resp):
            _run(validator.validate())
        emissions.clear()
        validator.mark_provider_failed("anthropic", "auth_failed", "boom")
        self.assertEqual(emissions[-1]["state"], "failed")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
        },
        clear=True,
    )
    def test_no_change_when_provider_doesnt_match(self):
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        emissions = []
        validator = ConfigValidator(broadcast=emissions.append)
        with patch.object(requests, "get", return_value=resp):
            _run(validator.validate())
        emissions.clear()
        validator.mark_provider_failed("openai", "auth_failed", "boom")
        # No change → no emission; coordinator row stays PASSED.
        self.assertEqual(emissions, [])
        coord = next(
            r for r in validator.current().results if r.agent == "coordinator"
        )
        self.assertTrue(coord.ok)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY_ANTHROPIC": "sk-ant",
            "LLM_MODEL_ARBITER": "anthropic/claude-x",
            "ENABLE_INTERRUPT_ARBITER": "true",
        },
        clear=True,
    )
    def test_flips_every_matching_agent(self):
        """Two agents share the same provider; both flip on a runtime
        failure for that provider."""
        resp = _fake_response(200, {"data": [{"id": "claude-x"}]})
        validator = ConfigValidator()
        with patch.object(requests, "get", return_value=resp):
            _run(validator.validate())
        validator.mark_provider_failed("anthropic", "auth_failed", "boom")
        coord = next(
            r for r in validator.current().results if r.agent == "coordinator"
        )
        arbiter = next(
            r for r in validator.current().results if r.agent == "arbiter"
        )
        self.assertFalse(coord.ok)
        self.assertFalse(arbiter.ok)


class TestBackendChecks(unittest.TestCase):
    """Reachability checks for non-LLM backends. Silent for backends
    the user hasn't configured (web search disabled, no TTS URL)."""

    def setUp(self):
        reset_settings_for_tests()
        # Probes retry on network errors (see TestBackendProbeRetry).
        # Tests here cover one-shot behaviour, so collapse to a single
        # attempt to keep them fast and intent-obvious.
        self._attempts_patch = patch.object(
            config_validation, "_BACKEND_PROBE_ATTEMPTS", 1,
        )
        self._attempts_patch.start()

    def tearDown(self):
        self._attempts_patch.stop()
        reset_settings_for_tests()

    @patch.dict(os.environ, {"LLM_MODEL": "anthropic/claude-x"}, clear=True)
    def test_no_backends_configured_returns_empty(self):
        from app.settings import get_settings
        results = config_validation._check_backends(get_settings())
        self.assertEqual(results, [])

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "brave",
            "BRAVE_API_KEY": "sk-brave",
        },
        clear=True,
    )
    def test_brave_with_key_passes_unverified(self):
        from app.settings import get_settings
        results = config_validation._check_backends(get_settings())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].label, "Brave Search")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "brave",
        },
        clear=True,
    )
    def test_brave_without_key_fails(self):
        from app.settings import get_settings
        results = config_validation._check_backends(get_settings())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "missing_credential")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://localhost:8888",
        },
        clear=True,
    )
    def test_searxng_reachable(self):
        resp = _fake_response(200, {})
        from app.settings import get_settings
        with patch.object(requests, "get", return_value=resp):
            results = config_validation._check_backends(get_settings())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://localhost:8888",
        },
        clear=True,
    )
    def test_searxng_network_failure(self):
        from app.settings import get_settings
        with patch.object(
            requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            results = config_validation._check_backends(get_settings())
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "network")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "TTS_URL": "localhost:9998",
        },
        clear=True,
    )
    def test_tts_reachable_when_socket_accepts(self):
        """Any TCP service that accepts a connect on the configured
        port counts as reachable — we don't try the full TTS protocol
        as a healthcheck."""
        import socket

        from app.settings import get_settings

        class _DummyConn:
            def __enter__(self): return self
            def __exit__(self, *_): pass

        with patch.object(
            socket, "create_connection", return_value=_DummyConn(),
        ):
            results = config_validation._check_backends(get_settings())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].backend, "tts")
        self.assertTrue(results[0].ok)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "TTS_URL": "localhost:9998",
        },
        clear=True,
    )
    def test_tts_unreachable_when_socket_refuses(self):
        import socket

        from app.settings import get_settings

        with patch.object(
            socket, "create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            results = config_validation._check_backends(get_settings())
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "network")

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "TTS_URL": "localhost",
        },
        clear=True,
    )
    def test_tts_malformed_url_reports_parse_error(self):
        """A truly malformed URL (missing port) surfaces as a parse
        error, not a network failure mislabel."""
        from app.settings import get_settings
        results = config_validation._check_backends(get_settings())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "other")
        self.assertIn("port", results[0].detail.lower())


class TestBackendProbeRetry(unittest.TestCase):
    """Backend probes retry on transient network errors so a Docker-
    stack startup race (agent boots before SearXNG/TTS is listening)
    self-resolves without the user having to click Test."""

    def setUp(self):
        reset_settings_for_tests()
        # Retry is Docker-only (the sibling-service co-start race), so
        # force Docker mode for these retry-path tests.
        self._docker_patch = patch.object(
            config_validation, "_running_in_docker", return_value=True,
        )
        self._docker_patch.start()
        # Don't actually sleep between retry attempts — same retry
        # logic, instant. The `time` module is the real one (the
        # probe uses ``time.sleep`` directly); patching it on the
        # validation module patches the in-use reference.
        self._sleep_patch = patch.object(config_validation.time, "sleep")
        self._sleep_mock = self._sleep_patch.start()
        # Keep _BACKEND_PROBE_ATTEMPTS at its default (5) here so we
        # exercise the real loop count.

    def tearDown(self):
        self._sleep_patch.stop()
        self._docker_patch.stop()
        reset_settings_for_tests()

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://searxng:8080",
        },
        clear=True,
    )
    def test_searxng_recovers_after_initial_failures(self):
        """First two probes fail with ConnectionError; third succeeds.
        Final result is ok — covers the startup-race case."""
        from app.settings import get_settings

        call_count = {"n": 0}

        def _maybe_raise(*_args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise requests.exceptions.ConnectionError("refused")
            return _fake_response(200, {})

        with patch.object(requests, "get", side_effect=_maybe_raise):
            results = config_validation._check_backends(get_settings())

        self.assertTrue(results[0].ok)
        self.assertEqual(call_count["n"], 3)
        # Slept twice (between attempts 1→2 and 2→3), not after the
        # successful third call.
        self.assertEqual(self._sleep_mock.call_count, 2)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://searxng:8080",
        },
        clear=True,
    )
    def test_searxng_gives_up_after_max_attempts(self):
        """All attempts fail with network error → last error returned,
        not an infinite loop."""
        from app.settings import get_settings

        with patch.object(
            requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ) as mock_get:
            results = config_validation._check_backends(get_settings())

        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "network")
        self.assertEqual(
            mock_get.call_count, config_validation._BACKEND_PROBE_ATTEMPTS,
        )

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://searxng:8080",
        },
        clear=True,
    )
    def test_searxng_does_not_retry_on_http_error(self):
        """HTTP 500 isn't transient — bad SearXNG config or a real
        server problem. Retrying would just delay surfacing it."""
        from app.settings import get_settings

        with patch.object(
            requests, "get", return_value=_fake_response(500, {}),
        ) as mock_get:
            results = config_validation._check_backends(get_settings())

        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "other")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(self._sleep_mock.call_count, 0)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "TTS_URL": "tts-server:9998",
        },
        clear=True,
    )
    def test_tts_recovers_after_initial_failures(self):
        import socket

        from app.settings import get_settings

        class _DummyConn:
            def __enter__(self): return self
            def __exit__(self, *_): pass

        call_count = {"n": 0}

        def _maybe_raise(*_args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionRefusedError("refused")
            return _DummyConn()

        with patch.object(socket, "create_connection", side_effect=_maybe_raise):
            results = config_validation._check_backends(get_settings())

        self.assertTrue(results[0].ok)
        self.assertEqual(call_count["n"], 3)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "TTS_URL": "tts-server:9998",
        },
        clear=True,
    )
    def test_tts_gives_up_after_max_attempts(self):
        import socket

        from app.settings import get_settings

        with patch.object(
            socket, "create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ) as mock_conn:
            results = config_validation._check_backends(get_settings())

        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "network")
        self.assertEqual(
            mock_conn.call_count, config_validation._BACKEND_PROBE_ATTEMPTS,
        )


class TestBackendProbeNoRetryNative(unittest.TestCase):
    """Outside Docker a down service won't come up mid-boot, so the probe
    runs exactly once — no retry loop, so a missing optional backend
    can't freeze startup."""

    def setUp(self):
        reset_settings_for_tests()
        self._docker_patch = patch.object(
            config_validation, "_running_in_docker", return_value=False,
        )
        self._docker_patch.start()
        self._sleep_patch = patch.object(config_validation.time, "sleep")
        self._sleep_mock = self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()
        self._docker_patch.stop()
        reset_settings_for_tests()

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://searxng:8080",
        },
        clear=True,
    )
    def test_searxng_probed_once_when_down(self):
        from app.settings import get_settings
        with patch.object(
            requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ) as mock_get:
            results = config_validation._check_backends(get_settings())
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].error_kind, "network")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(self._sleep_mock.call_count, 0)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "TTS_URL": "tts-server:9998",
        },
        clear=True,
    )
    def test_tts_probed_once_when_down(self):
        import socket

        from app.settings import get_settings
        with patch.object(
            socket, "create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ) as mock_conn:
            results = config_validation._check_backends(get_settings())
        self.assertFalse(results[0].ok)
        self.assertEqual(mock_conn.call_count, 1)
        self.assertEqual(self._sleep_mock.call_count, 0)


class TestUpdateBackend(unittest.TestCase):
    def setUp(self):
        from app.settings.validation import (
            BackendResult,
            ConfigValidator,
            ValidationState,
            ValidationStatus,
        )
        self.broadcast_calls = []
        self.validator = ConfigValidator(
            broadcast=lambda payload: self.broadcast_calls.append(payload),
        )
        self.validator._status = ValidationStatus(
            state=ValidationState.PASSED,
            results=[],
            backends=[
                BackendResult(backend="tts", label="F5-TTS server", ok=True),
            ],
        )
        self._BackendResult = BackendResult

    def test_no_emit_when_ok_unchanged(self):
        result = self._BackendResult(
            backend="tts", label="F5-TTS server", ok=True, detail="still ok",
        )
        emitted = self.validator.update_backend(result)
        self.assertFalse(emitted)
        self.assertEqual(self.broadcast_calls, [])

    def test_emits_when_ok_transitions(self):
        result = self._BackendResult(
            backend="tts", label="F5-TTS server", ok=False,
            error_kind="network", detail="refused",
        )
        emitted = self.validator.update_backend(result)
        self.assertTrue(emitted)
        self.assertEqual(len(self.broadcast_calls), 1)
        backends = self.broadcast_calls[0]["backends"]
        tts_entry = next(b for b in backends if b["backend"] == "tts")
        self.assertFalse(tts_entry["ok"])

    def test_adds_when_backend_missing(self):
        # The daemon's first probe must seed the entry rather than
        # silently skip when boot validation hasn't populated it yet.
        from app.settings.validation import (
            ConfigValidator,
            ValidationState,
            ValidationStatus,
        )
        fresh = ConfigValidator(
            broadcast=lambda payload: self.broadcast_calls.append(payload),
        )
        fresh._status = ValidationStatus(
            state=ValidationState.OPEN, results=[], backends=[],
        )
        before = len(self.broadcast_calls)
        result = self._BackendResult(
            backend="tts", label="F5-TTS server", ok=True, detail="reachable",
        )
        emitted = fresh.update_backend(result)
        self.assertTrue(emitted)
        self.assertEqual(len(self.broadcast_calls), before + 1)
        self.assertEqual(len(fresh.current().backends), 1)


class TestAgentResultPayload(unittest.TestCase):
    def test_to_dict_round_trip(self):
        r = AgentResult(
            agent="arbiter",
            enabled=True,
            provider="anthropic",
            model="anthropic/claude-x",
            inherits_coordinator=False,
            ok=True,
            detail="ok",
        )
        d = r.to_dict()
        self.assertEqual(d["agent"], "arbiter")
        self.assertEqual(d["error_kind"], None)
        self.assertEqual(d["not_validated"], False)


if __name__ == "__main__":
    unittest.main()
