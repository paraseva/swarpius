"""Settings-Test results persist into the live validation status for
backends — but only when the tested config matches what's saved, so the
Settings highlight clears/sets and survives a refresh without conflating
unsaved edits.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.settings.test_endpoint import persist_backend_test_result  # noqa: E402
from app.settings.validation import (  # noqa: E402
    get_validator,
    reset_validator_for_tests,
)


class TestPersistBackendTestResult(unittest.TestCase):
    def setUp(self):
        reset_validator_for_tests()

    def tearDown(self):
        reset_validator_for_tests()

    def _backends(self):
        return {b.backend: b for b in get_validator().current().backends}

    def test_persists_searxng_when_matches_saved(self):
        persist_backend_test_result(
            {"provider": "searxng", "matches_saved": True},
            {"ok": True, "detail": "Reachable"},
        )
        self.assertTrue(self._backends()["web-search"].ok)

    def test_persists_down_result(self):
        persist_backend_test_result(
            {"provider": "tts", "matches_saved": True},
            {"ok": False, "error_kind": "network", "detail": "refused"},
        )
        self.assertFalse(self._backends()["tts"].ok)

    def test_does_not_persist_when_config_differs_from_saved(self):
        persist_backend_test_result(
            {"provider": "searxng", "matches_saved": False},
            {"ok": True},
        )
        self.assertEqual(get_validator().current().backends, [])

    def test_does_not_persist_llm_provider(self):
        persist_backend_test_result(
            {"provider": "anthropic", "matches_saved": True},
            {"ok": True},
        )
        self.assertEqual(get_validator().current().backends, [])


if __name__ == "__main__":
    unittest.main()
