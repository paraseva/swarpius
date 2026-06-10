"""Feature-availability ``tts_available`` and ``tts_configured`` flags.

The browser uses these to decide what to show:

- ``tts_configured`` is True iff ``TTS_URL`` is set. Drives the
  "Not Configured" chip — stays steady even when the TTS server
  is momentarily unreachable.
- ``tts_available`` is True iff ``tts_configured`` AND the
  validator's most recent reachability probe found the TTS server
  alive (``backend='tts'``, ``ok=True``). Drives the red/green
  health indicator and whether auto-TTS fires.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


def _make_runtime():
    """Build a minimal RuntimeState for testing the availability
    payload. We don't need the full init machinery — we set just the
    attributes the payload reads."""
    from app.runtime.state import RuntimeState

    rs = object.__new__(RuntimeState)
    rs.roon_state = "paired"
    rs.roon_status_message = ""
    rs.roon_failure_reason = None
    rs.stop_marker_coordinator = None
    return rs


def _stub_validator_backends(backends):
    """Patch the validator singleton to expose the given backends list."""
    from app.settings import validation as config_validation
    config_validation.reset_validator_for_tests()
    validator = config_validation.get_validator()
    validator._status = config_validation.ValidationStatus(
        state=config_validation.ValidationState.PASSED,
        results=[],
        backends=backends,
    )
    return validator


class TestTtsAvailable(unittest.TestCase):

    def setUp(self):
        from app.settings import reset_settings_for_tests
        from app.settings.validation import reset_validator_for_tests
        reset_settings_for_tests()
        reset_validator_for_tests()

    def tearDown(self):
        from app.settings import reset_settings_for_tests
        from app.settings.validation import reset_validator_for_tests
        reset_settings_for_tests()
        reset_validator_for_tests()

    @patch.dict(os.environ, {"LLM_MODEL": "anthropic/x"}, clear=True)
    def test_false_when_tts_url_unset(self):
        _stub_validator_backends([])
        rs = _make_runtime()
        payload = rs.get_feature_availability_payload()
        self.assertFalse(payload["tts_available"])
        self.assertFalse(payload["tts_configured"])

    @patch.dict(
        os.environ,
        {"LLM_MODEL": "anthropic/x", "TTS_URL": "localhost:9998"},
        clear=True,
    )
    def test_configured_stays_true_when_probe_fails(self):
        # ``tts_configured`` tracks URL-set only; a flapping TTS server
        # must not toggle the "Not Configured" chip.
        from app.settings.validation import BackendResult
        _stub_validator_backends([
            BackendResult(
                backend="tts", label="F5-TTS server",
                ok=False, error_kind="network", detail="refused",
            ),
        ])
        rs = _make_runtime()
        payload = rs.get_feature_availability_payload()
        self.assertTrue(payload["tts_configured"])
        self.assertFalse(payload["tts_available"])

    @patch.dict(
        os.environ,
        {"LLM_MODEL": "anthropic/x", "TTS_URL": "localhost:9998"},
        clear=True,
    )
    def test_false_when_tts_url_set_but_backend_probe_failed(self):
        from app.settings.validation import BackendResult
        _stub_validator_backends([
            BackendResult(
                backend="tts", label="F5-TTS server",
                ok=False, error_kind="network",
                detail="refused",
            ),
        ])
        rs = _make_runtime()
        payload = rs.get_feature_availability_payload()
        self.assertFalse(payload["tts_available"])

    @patch.dict(
        os.environ,
        {"LLM_MODEL": "anthropic/x", "TTS_URL": "localhost:9998"},
        clear=True,
    )
    def test_true_when_url_set_and_backend_probe_passed(self):
        from app.settings.validation import BackendResult
        _stub_validator_backends([
            BackendResult(
                backend="tts", label="F5-TTS server",
                ok=True, detail="reachable",
            ),
        ])
        rs = _make_runtime()
        payload = rs.get_feature_availability_payload()
        self.assertTrue(payload["tts_available"])

    @patch.dict(
        os.environ,
        {"LLM_MODEL": "anthropic/x", "TTS_URL": "localhost:9998"},
        clear=True,
    )
    def test_false_when_validator_hasnt_run_yet(self):
        """Boot-time pre-validation state: TTS_URL is set but no probe
        result is in the validator yet. Browser shouldn't try TTS."""
        _stub_validator_backends([])
        rs = _make_runtime()
        payload = rs.get_feature_availability_payload()
        self.assertFalse(payload["tts_available"])


class TestConfigPristinePayload(unittest.TestCase):
    """The payload carries ``config_pristine`` so the browser can show
    the first-run Getting Started intro until the user sets something."""

    def setUp(self):
        from app.settings import reset_settings_for_tests
        from app.settings.validation import reset_validator_for_tests
        reset_settings_for_tests()
        reset_validator_for_tests()

    def tearDown(self):
        from app.settings import reset_settings_for_tests
        from app.settings.validation import reset_validator_for_tests
        reset_settings_for_tests()
        reset_validator_for_tests()

    @patch.dict(os.environ, {"LLM_MODEL": "anthropic/x"}, clear=True)
    def test_payload_reflects_config_pristine_helper(self):
        _stub_validator_backends([])
        rs = _make_runtime()
        for pristine in (True, False):
            with patch(
                "app.settings.endpoints.config_pristine", return_value=pristine,
            ):
                payload = rs.get_feature_availability_payload()
            self.assertEqual(payload["config_pristine"], pristine)


class TestIsBundlePayload(unittest.TestCase):
    """The payload carries ``is_bundle`` so the browser can show
    bundle-only guidance (e.g. the stop-marker setup steps and the
    'open the folder' button, which only make sense on the desktop app
    where the agent and browser share a machine)."""

    def setUp(self):
        from app.settings import reset_settings_for_tests
        from app.settings.validation import reset_validator_for_tests
        reset_settings_for_tests()
        reset_validator_for_tests()

    def tearDown(self):
        from app.settings import reset_settings_for_tests
        from app.settings.validation import reset_validator_for_tests
        reset_settings_for_tests()
        reset_validator_for_tests()

    @patch.dict(os.environ, {"LLM_MODEL": "anthropic/x"}, clear=True)
    def test_payload_reflects_running_from_bundle(self):
        _stub_validator_backends([])
        rs = _make_runtime()
        for is_bundle in (True, False):
            with patch(
                "app.runtime.state._running_from_bundle", return_value=is_bundle,
            ):
                payload = rs.get_feature_availability_payload()
            self.assertEqual(payload["is_bundle"], is_bundle)


if __name__ == "__main__":
    unittest.main()
