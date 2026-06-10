"""Tavily provider for the ``web_search`` tool.

Uses Tavily's ``/search`` endpoint, which is POST + JSON. The free
tier covers casual usage; ranking is handled server-side, so the
canonical 4-field shape needs no client-side scoring. Tavily has no
notion of category (web search only) — the parameter is accepted but
not forwarded.
"""

from typing import List, Optional

import aiohttp

from app.exceptions import ExternalServiceError
from tools.web_search.base import WebSearchTool, WebSearchToolConfig


class TavilySearchToolConfig(WebSearchToolConfig):
    api_key: str = ""


class TavilySearchTool(WebSearchTool):
    """Tavily-backed web search."""

    provider_name = "tavily"
    BASE_URL = "https://api.tavily.com/search"

    def __init__(self, config: TavilySearchToolConfig = TavilySearchToolConfig()):
        super().__init__(config)
        self.api_key = config.api_key

    async def _fetch_search_results(
        self,
        session: aiohttp.ClientSession,
        query: str,
        category: Optional[str],
    ) -> List[dict]:
        if not self.api_key:
            raise ValueError("Tavily api_key must be provided in the config.")

        _ = category  # Tavily has no category dimension

        body = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max(self.max_results, 10),
        }

        try:
            async with session.post(self.BASE_URL, json=body) as response:
                if response.status != 200:
                    text = await response.text()
                    raise ExternalServiceError(
                        f"Tavily Search failed for '{query}': "
                        f"{response.status} {response.reason} — {text[:200]}",
                    )
                data = await response.json()
        except aiohttp.ClientError as exc:
            raise ExternalServiceError(
                f"Tavily Search unreachable: {exc}",
            ) from exc

        results = data.get("results") or []
        return [
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "content": item.get("content"),
                "query": query,
            }
            for item in results
            if item.get("url") and item.get("title")
        ]
