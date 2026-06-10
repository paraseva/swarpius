"""Tests for the Tavily Search provider."""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.exceptions import ExternalServiceError  # noqa: E402
from tools.web_search import (  # noqa: E402
    TavilySearchTool,
    TavilySearchToolConfig,
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
        self.last_json: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, json=None):
        self.last_url = url
        self.last_json = dict(json or {})
        return self._response


_VALID_TAVILY_PAYLOAD = {
    "answer": "Kate Bush is an English singer.",
    "results": [
        {
            "url": "https://en.wikipedia.org/wiki/Kate_Bush",
            "title": "Kate Bush — Wikipedia",
            "content": "English singer and songwriter.",
            "score": 0.97,
        },
        {
            "url": "https://www.katebush.com/",
            "title": "Kate Bush — Official site",
            "content": "Official website.",
            "score": 0.91,
        },
    ],
}


class TestTavilyFetchMapping(unittest.IsolatedAsyncioTestCase):
    async def test_maps_tavily_payload_to_canonical_dicts(self):
        tool = TavilySearchTool(TavilySearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(200, _VALID_TAVILY_PAYLOAD))

        results = await tool._fetch_search_results(session, "kate bush", "general")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], {
            "url": "https://en.wikipedia.org/wiki/Kate_Bush",
            "title": "Kate Bush — Wikipedia",
            "content": "English singer and songwriter.",
            "query": "kate bush",
        })

    async def test_passes_api_key_in_post_body(self):
        tool = TavilySearchTool(TavilySearchToolConfig(api_key="secret-key"))
        session = _FakeSession(_FakeResponse(200, _VALID_TAVILY_PAYLOAD))

        await tool._fetch_search_results(session, "kate bush", "general")

        self.assertEqual(session.last_json.get("api_key"), "secret-key")
        self.assertEqual(session.last_json.get("query"), "kate bush")

    async def test_drops_results_missing_url_or_title(self):
        payload = {
            "results": [
                {"url": "https://a.com", "title": "Has both", "content": "OK"},
                {"url": "", "title": "no url", "content": "drop"},
                {"url": "https://b.com", "title": "", "content": "drop"},
                {"content": "no url no title"},
            ],
        }
        tool = TavilySearchTool(TavilySearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(200, payload))

        results = await tool._fetch_search_results(session, "q", "general")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://a.com")

    async def test_handles_empty_results(self):
        tool = TavilySearchTool(TavilySearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(200, {"results": []}))

        results = await tool._fetch_search_results(session, "q", "general")
        self.assertEqual(results, [])

    async def test_non_200_raises_external_service_error(self):
        tool = TavilySearchTool(TavilySearchToolConfig(api_key="abc"))
        session = _FakeSession(_FakeResponse(401, {"error": "unauth"}, reason="Unauthorized"))

        with self.assertRaises(ExternalServiceError) as ctx:
            await tool._fetch_search_results(session, "q", "general")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("Unauthorized", str(ctx.exception))

    async def test_connection_error_named_in_wrapped_exception(self):
        import aiohttp

        tool = TavilySearchTool(TavilySearchToolConfig(api_key="abc"))

        class _FailingSession:
            def post(self, *args, **kwargs):
                raise aiohttp.ClientConnectorError(
                    connection_key=None, os_error=OSError("Connection refused"),
                )

        with self.assertRaises(ExternalServiceError) as ctx:
            await tool._fetch_search_results(_FailingSession(), "q", "general")
        msg = str(ctx.exception)
        self.assertIn("Tavily", msg)
        self.assertTrue(
            "unreachable" in msg.lower() or "connect" in msg.lower(),
            f"expected unreachable/connect framing in {msg!r}",
        )

    async def test_missing_api_key_raises_value_error(self):
        tool = TavilySearchTool(TavilySearchToolConfig(api_key=""))
        session = _FakeSession(_FakeResponse(200, _VALID_TAVILY_PAYLOAD))

        with self.assertRaises(ValueError):
            await tool._fetch_search_results(session, "q", "general")


if __name__ == "__main__":
    unittest.main()
