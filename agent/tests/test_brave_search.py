"""Tests for the Brave Search provider."""

import asyncio
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.exceptions import ExternalServiceError  # noqa: E402
from tools.web_search import (  # noqa: E402
    BraveSearchTool,
    BraveSearchToolConfig,
    WebSearchToolInputSchema,
)


class _FakeResponse:
    def __init__(self, status: int, payload: dict, reason: str = "OK"):
        self.status = status
        self.reason = reason
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_url: str = ""
        self.last_params: dict = {}
        self.last_headers: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, *, params=None, headers=None):
        self.last_url = url
        self.last_params = dict(params or {})
        self.last_headers = dict(headers or {})
        return self._response


_VALID_BRAVE_PAYLOAD = {
    "web": {
        "results": [
            {
                "url": "https://en.wikipedia.org/wiki/Kate_Bush",
                "title": "Kate Bush - Wikipedia",
                "description": "English singer and songwriter.",
            },
            {
                "url": "https://www.katebush.com/",
                "title": "Kate Bush — Official site",
                "description": "Official Kate Bush website.",
            },
        ],
    },
}


class TestBraveFetchMapping(unittest.IsolatedAsyncioTestCase):
    """Brave's web.results[] should map to the canonical 4-field shape."""

    async def test_maps_brave_payload_to_canonical_dicts(self):
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc123"))
        session = _FakeSession(_FakeResponse(200, _VALID_BRAVE_PAYLOAD))

        results = await tool._fetch_search_results(session, "kate bush", "general")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], {
            "url": "https://en.wikipedia.org/wiki/Kate_Bush",
            "title": "Kate Bush - Wikipedia",
            "content": "English singer and songwriter.",
            "query": "kate bush",
        })
        self.assertEqual(results[1]["query"], "kate bush")

    async def test_passes_api_key_in_subscription_header(self):
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="secret-key"))
        session = _FakeSession(_FakeResponse(200, _VALID_BRAVE_PAYLOAD))

        await tool._fetch_search_results(session, "kate bush", "general")

        self.assertEqual(session.last_headers.get("X-Subscription-Token"), "secret-key")
        self.assertEqual(session.last_params.get("q"), "kate bush")

    async def test_drops_results_missing_url_or_title(self):
        payload = {
            "web": {
                "results": [
                    {"url": "https://a.com", "title": "Has both", "description": "OK"},
                    {"url": "", "title": "Missing url", "description": "drop"},
                    {"url": "https://b.com", "title": "", "description": "drop"},
                    {"description": "no url no title"},
                ],
            },
        }
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(200, payload))

        results = await tool._fetch_search_results(session, "q", "general")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://a.com")

    async def test_handles_empty_brave_response(self):
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(200, {"web": {"results": []}}))

        results = await tool._fetch_search_results(session, "q", "general")
        self.assertEqual(results, [])

    async def test_non_200_raises_external_service_error(self):
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(429, {"error": "rate limited"}, reason="Too Many Requests"))

        with self.assertRaises(ExternalServiceError) as ctx:
            await tool._fetch_search_results(session, "q", "general")
        self.assertIn("429", str(ctx.exception))
        self.assertIn("Too Many Requests", str(ctx.exception))

    async def test_connection_error_named_in_wrapped_exception(self):
        """DNS / refused / network-down errors must surface with the
        provider name so the coordinator can plan around it (e.g. fall
        back to a different web-search backend, or apologise about
        web search specifically rather than reporting a generic
        connection error)."""
        import aiohttp

        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc"))

        class _FailingSession:
            def get(self, *args, **kwargs):
                raise aiohttp.ClientConnectorError(
                    connection_key=None, os_error=OSError("Connection refused"),
                )

        with self.assertRaises(ExternalServiceError) as ctx:
            await tool._fetch_search_results(_FailingSession(), "q", "general")
        msg = str(ctx.exception)
        self.assertIn("Brave Search", msg)
        self.assertTrue(
            "unreachable" in msg.lower() or "connect" in msg.lower(),
            f"expected unreachable/connect framing in {msg!r}",
        )

    async def test_missing_api_key_raises_value_error(self):
        tool = BraveSearchTool(BraveSearchToolConfig(api_key=""))
        session = _FakeSession(_FakeResponse(200, _VALID_BRAVE_PAYLOAD))

        with self.assertRaises(ValueError):
            await tool._fetch_search_results(session, "q", "general")


class TestBraveRunAsync(unittest.IsolatedAsyncioTestCase):
    """Brave inherits the base run_async — dedup, max_results, schema build."""

    async def test_dedups_across_queries(self):
        # Same URL returned for two different queries — first one wins
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc"))

        async def fake_fetch(session, query, category):
            _ = (session, category)
            return [
                {
                    "url": "https://shared.example.com",
                    "title": f"Result for {query}",
                    "content": f"From {query}",
                    "query": query,
                },
            ]

        tool._fetch_search_results = fake_fetch
        params = WebSearchToolInputSchema(
            queries=["query one", "query two"], category="general",
        )
        output = await tool.run_async(params)

        self.assertEqual(len(output.results), 1)
        self.assertEqual(output.results[0].query, "query one")

    async def test_respects_max_results_limit(self):
        tool = BraveSearchTool(BraveSearchToolConfig(api_key="abc", max_results=2))

        async def fake_fetch(session, query, category):
            _ = (session, category)
            return [
                {"url": f"https://r{i}.example.com", "title": f"R{i}",
                 "content": "x", "query": query}
                for i in range(5)
            ]

        tool._fetch_search_results = fake_fetch
        params = WebSearchToolInputSchema(queries=["q"], category="general")
        output = await tool.run_async(params)

        self.assertEqual(len(output.results), 2)


if __name__ == "__main__":
    asyncio.run(unittest.main())
