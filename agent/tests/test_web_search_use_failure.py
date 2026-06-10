"""A web_search failure at use-time flags the web-search backend down in
the live validation status, so the Settings highlight reflects a dead
Brave/Tavily key (or unreachable SearXNG) without waiting for a manual
Test. Recovery is via the health loop (SearXNG) or the Test button.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.exceptions import ExternalServiceError  # noqa: E402
from app.settings.validation import (  # noqa: E402
    get_validator,
    reset_validator_for_tests,
)
from tools.web_search.base import (  # noqa: E402
    WebSearchTool,
    WebSearchToolConfig,
    WebSearchToolInputSchema,
)


class _FailingTool(WebSearchTool):
    provider_name = "searxng"

    async def _fetch_search_results(self, session, query, category):
        raise ExternalServiceError("SearXNG", "503 Service Unavailable")


class _OkTool(WebSearchTool):
    provider_name = "searxng"

    async def _fetch_search_results(self, session, query, category):
        return [{"url": "http://x", "title": "t", "content": "c", "query": query}]


class TestUseFailureMarksBackendDown(unittest.TestCase):
    def setUp(self):
        reset_validator_for_tests()

    def tearDown(self):
        reset_validator_for_tests()

    def test_search_failure_marks_web_search_down_and_reraises(self):
        tool = _FailingTool(WebSearchToolConfig())
        with self.assertRaises(ExternalServiceError):
            asyncio.run(tool.run_async(WebSearchToolInputSchema(queries=["x"])))
        web = next(b for b in get_validator().current().backends
                   if b.backend == "web-search")
        self.assertFalse(web.ok)

    def test_successful_search_leaves_status_untouched(self):
        """Down-on-failure only — a success doesn't write status (recovery
        is the loop / Test button)."""
        tool = _OkTool(WebSearchToolConfig())
        out = asyncio.run(
            tool.run_async(WebSearchToolInputSchema(queries=["x"])))
        self.assertTrue(out.results)
        self.assertEqual(get_validator().current().backends, [])


if __name__ == "__main__":
    unittest.main()
