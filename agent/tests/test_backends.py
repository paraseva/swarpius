"""Backend reachability registry — the single source of truth for which
non-LLM backends are active, how to probe them, and whether they're safe
to poll. Consumed by validation, the health loop, and the Test endpoint.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.settings import get_settings, reset_settings_for_tests  # noqa: E402
from app.settings.backends import (  # noqa: E402
    active_backend_checks,
    backend_for_provider,
)


class TestActiveBackendChecks(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
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
    def test_searxng_is_pollable_web_search(self):
        checks = active_backend_checks(get_settings())
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].backend_id, "web-search")
        self.assertTrue(checks[0].pollable)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "brave",
            "BRAVE_API_KEY": "k",
        },
        clear=True,
    )
    def test_brave_is_not_pollable(self):
        """A real Brave probe spends a query — never poll it."""
        checks = active_backend_checks(get_settings())
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].backend_id, "web-search")
        self.assertFalse(checks[0].pollable)

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "tavily",
            "TAVILY_API_KEY": "k",
        },
        clear=True,
    )
    def test_tavily_is_not_pollable(self):
        checks = active_backend_checks(get_settings())
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].pollable)

    @patch.dict(
        os.environ,
        {"LLM_MODEL": "anthropic/claude-x", "TTS_URL": "tts:9998"},
        clear=True,
    )
    def test_tts_is_pollable(self):
        checks = active_backend_checks(get_settings())
        self.assertEqual([c.backend_id for c in checks], ["tts"])
        self.assertTrue(checks[0].pollable)

    @patch.dict(
        os.environ, {"LLM_MODEL": "anthropic/claude-x"}, clear=True,
    )
    def test_nothing_configured_yields_no_checks(self):
        self.assertEqual(active_backend_checks(get_settings()), [])

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://s:8080",
            "TTS_URL": "tts:9998",
        },
        clear=True,
    )
    def test_searxng_and_tts_both_active(self):
        ids = {c.backend_id for c in active_backend_checks(get_settings())}
        self.assertEqual(ids, {"web-search", "tts"})


class TestBackendForProvider(unittest.TestCase):
    def test_maps_each_backend_provider(self):
        self.assertEqual(
            backend_for_provider("searxng"), ("web-search", "SearXNG"))
        self.assertEqual(
            backend_for_provider("brave"), ("web-search", "Brave Search"))
        self.assertEqual(
            backend_for_provider("tavily"), ("web-search", "Tavily"))
        self.assertEqual(
            backend_for_provider("tts"), ("tts", "F5-TTS server"))

    def test_llm_provider_is_not_a_backend(self):
        self.assertIsNone(backend_for_provider("anthropic"))
        self.assertIsNone(backend_for_provider(""))


if __name__ == "__main__":
    unittest.main()
