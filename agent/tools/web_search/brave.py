"""Brave Search provider for the ``web_search`` tool.

Uses Brave's Web Search API (``api.search.brave.com``). The free tier
covers the ``web/search`` endpoint, which is sufficient for the kind
of factual artist/album/genre lookups Swarpius drives. The ``category``
parameter is accepted but not currently mapped to Brave's separate
news endpoint — Brave's web results already mix in news content
where relevant.
"""

from typing import List, Optional

import aiohttp

from app.exceptions import ExternalServiceError
from tools.web_search.base import WebSearchTool, WebSearchToolConfig


class BraveSearchToolConfig(WebSearchToolConfig):
    api_key: str = ""


class BraveSearchTool(WebSearchTool):
    """Brave Search-backed web search."""

    provider_name = "brave"
    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, config: BraveSearchToolConfig = BraveSearchToolConfig()):
        super().__init__(config)
        self.api_key = config.api_key

    async def _fetch_search_results(
        self,
        session: aiohttp.ClientSession,
        query: str,
        category: Optional[str],
    ) -> List[dict]:
        if not self.api_key:
            raise ValueError("Brave Search api_key must be provided in the config.")

        params = {
            "q": query,
            "count": str(max(self.max_results, 10)),
        }
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        try:
            async with session.get(
                self.BASE_URL, params=params, headers=headers,
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise ExternalServiceError(
                        f"Brave Search failed for '{query}': "
                        f"{response.status} {response.reason} — {body[:200]}",
                    )
                data = await response.json()
        except aiohttp.ClientError as exc:
            raise ExternalServiceError(
                f"Brave Search unreachable: {exc}",
            ) from exc

        web_results = (data.get("web") or {}).get("results") or []
        return [
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "content": item.get("description"),
                "query": query,
            }
            for item in web_results
            if item.get("url") and item.get("title")
        ]
