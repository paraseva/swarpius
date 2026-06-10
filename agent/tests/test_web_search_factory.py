"""Tests for the web-search backend factory in runtime_state."""

import unittest
from dataclasses import dataclass
from typing import Optional

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.runtime.state import _build_web_search_tool  # noqa: E402
from tools.web_search import (  # noqa: E402
    BraveSearchTool,
    SearXNGSearchTool,
    TavilySearchTool,
)


@dataclass
class _FakeSettings:
    web_search_provider: Optional[str] = None
    searxng_url: Optional[str] = None
    brave_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None


class TestBuildWebSearchTool(unittest.TestCase):
    def test_explicit_none_disables_search_even_with_credentials(self):
        settings = _FakeSettings(
            web_search_provider="none",
            searxng_url="http://searxng:8080",
            brave_api_key="abc",
        )
        self.assertIsNone(_build_web_search_tool(settings))

    def test_credentials_without_provider_do_not_enable_search(self):
        """Credentials alone must not enable search — WEB_SEARCH_PROVIDER
        is required."""
        settings = _FakeSettings(
            searxng_url="http://searxng:8080",
            brave_api_key="abc",
            tavily_api_key="xyz",
        )
        self.assertIsNone(_build_web_search_tool(settings))

    def test_unknown_provider_disables_search(self):
        settings = _FakeSettings(web_search_provider="not-a-thing")
        self.assertIsNone(_build_web_search_tool(settings))

    def test_provider_match_is_case_insensitive(self):
        settings = _FakeSettings(
            web_search_provider="BRAVE", brave_api_key="abc",
        )
        self.assertIsInstance(_build_web_search_tool(settings), BraveSearchTool)

    def test_empty_string_provider_treated_as_unset(self):
        settings = _FakeSettings(
            web_search_provider="   ", brave_api_key="abc",
        )
        self.assertIsNone(_build_web_search_tool(settings))


class TestExplicitProvider(unittest.TestCase):
    def test_explicit_searxng(self):
        settings = _FakeSettings(
            web_search_provider="searxng",
            searxng_url="http://searxng:8080",
        )
        tool = _build_web_search_tool(settings)
        self.assertIsInstance(tool, SearXNGSearchTool)
        self.assertEqual(tool.base_url, "http://searxng:8080")

    def test_explicit_searxng_disabled_when_url_missing(self):
        settings = _FakeSettings(web_search_provider="searxng")
        self.assertIsNone(_build_web_search_tool(settings))

    def test_explicit_brave(self):
        settings = _FakeSettings(
            web_search_provider="brave", brave_api_key="abc",
        )
        tool = _build_web_search_tool(settings)
        self.assertIsInstance(tool, BraveSearchTool)
        self.assertEqual(tool.api_key, "abc")

    def test_explicit_brave_disabled_when_api_key_missing(self):
        settings = _FakeSettings(web_search_provider="brave")
        self.assertIsNone(_build_web_search_tool(settings))

    def test_explicit_tavily(self):
        settings = _FakeSettings(
            web_search_provider="tavily", tavily_api_key="xyz",
        )
        tool = _build_web_search_tool(settings)
        self.assertIsInstance(tool, TavilySearchTool)
        self.assertEqual(tool.api_key, "xyz")

    def test_explicit_tavily_disabled_when_api_key_missing(self):
        settings = _FakeSettings(web_search_provider="tavily")
        self.assertIsNone(_build_web_search_tool(settings))


class TestStartupVisibility(unittest.TestCase):
    """The factory must always emit a 'Web search backend: ...' log line
    so the chosen backend is verifiable from console output."""

    def test_explicit_brave_logs_chosen_backend(self):
        settings = _FakeSettings(
            web_search_provider="brave", brave_api_key="abc",
        )
        with self.assertLogs("swarpius.runtime", level="INFO") as cm:
            tool = _build_web_search_tool(settings)
        self.assertIsInstance(tool, BraveSearchTool)
        self.assertTrue(
            any("Web search backend: brave" in m for m in cm.output),
            f"expected 'Web search backend: brave' in logs, got: {cm.output}",
        )
        self.assertTrue(
            any("WEB_SEARCH_PROVIDER=brave" in m for m in cm.output),
            "expected the provider source to be logged",
        )

    def test_explicit_searxng_logs_chosen_backend(self):
        settings = _FakeSettings(
            web_search_provider="searxng",
            searxng_url="http://searxng:8080",
        )
        with self.assertLogs("swarpius.runtime", level="INFO") as cm:
            _build_web_search_tool(settings)
        self.assertTrue(
            any("Web search backend: searxng" in m for m in cm.output),
        )

    def test_explicit_none_logs_disabled(self):
        settings = _FakeSettings(web_search_provider="none")
        with self.assertLogs("swarpius.runtime", level="INFO") as cm:
            tool = _build_web_search_tool(settings)
        self.assertIsNone(tool)
        self.assertTrue(
            any("Web search backend: disabled" in m for m in cm.output),
        )

    def test_no_provider_logs_disabled(self):
        settings = _FakeSettings()
        with self.assertLogs("swarpius.runtime", level="INFO") as cm:
            tool = _build_web_search_tool(settings)
        self.assertIsNone(tool)
        self.assertTrue(
            any("Web search backend: disabled" in m for m in cm.output),
        )

    def test_explicit_provider_with_missing_credential_logs_warning(self):
        settings = _FakeSettings(web_search_provider="brave")
        with self.assertLogs("swarpius.runtime", level="WARNING") as cm:
            tool = _build_web_search_tool(settings)
        self.assertIsNone(tool)
        self.assertTrue(
            any("WEB_SEARCH_PROVIDER=brave" in m and "BRAVE_API_KEY" in m
                for m in cm.output),
        )

    def test_searxng_missing_url_logs_warning(self):
        settings = _FakeSettings(web_search_provider="searxng")
        with self.assertLogs("swarpius.runtime", level="WARNING") as cm:
            tool = _build_web_search_tool(settings)
        self.assertIsNone(tool)
        self.assertTrue(
            any("WEB_SEARCH_PROVIDER=searxng" in m and "SEARXNG_URL" in m
                for m in cm.output),
        )


if __name__ == "__main__":
    unittest.main()
