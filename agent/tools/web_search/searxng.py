"""SearXNG provider for the ``web_search`` tool."""

from typing import List, Optional

import aiohttp

from app.exceptions import ExternalServiceError
from tools.web_search.base import WebSearchTool, WebSearchToolConfig


class SearXNGSearchToolConfig(WebSearchToolConfig):
    base_url: str = ""


class SearXNGSearchTool(WebSearchTool):
    """SearXNG-backed web search."""

    provider_name = "searxng"

    def __init__(self, config: SearXNGSearchToolConfig = SearXNGSearchToolConfig()):
        super().__init__(config)
        self.base_url = config.base_url

    async def _fetch_search_results(
        self,
        session: aiohttp.ClientSession,
        query: str,
        category: Optional[str],
    ) -> List[dict]:
        if not self.base_url:
            raise ValueError("SearXNG base_url must be provided in the config.")

        query_params = {
            "q": query,
            "safesearch": "0",
            "format": "json",
            "language": "en",
            "engines": "bing,duckduckgo,google,startpage,yandex",
        }
        if category:
            query_params["categories"] = category

        try:
            async with session.get(
                f"{self.base_url}/search", params=query_params,
            ) as response:
                if response.status != 200:
                    raise ExternalServiceError(
                        f"SearXNG search failed for query '{query}': "
                        f"{response.status} {response.reason}",
                    )
                data = await response.json()
        except aiohttp.ClientError as exc:
            # DNS failure / connection refused / network down — coordinator
            # needs the provider name framed explicitly so it can apologise
            # about web search specifically rather than a generic error.
            raise ExternalServiceError(
                f"SearXNG unreachable at {self.base_url}: {exc}",
            ) from exc

        results = data.get("results", [])
        for result in results:
            result["query"] = query
        return results

    def _post_process(
        self,
        flat: List[dict],
        category: Optional[str],
    ) -> List[dict]:
        sorted_results = sorted(
            flat, key=lambda x: x.get("score", 0), reverse=True,
        )

        seen_urls: set[str] = set()
        unique: list[dict] = []
        for result in sorted_results:
            if {"content", "title", "url", "query"} - result.keys():
                continue
            if result["url"] in seen_urls:
                continue
            title = result["title"]
            if "metadata" in result:
                title = f"{title} - (Published {result['metadata']})"
            if result.get("publishedDate"):
                title = f"{title} - (Published {result['publishedDate']})"
            unique.append({**result, "title": title})
            seen_urls.add(result["url"])

        if category:
            unique = [r for r in unique if r.get("category") == category]

        return [
            {
                "url": r["url"],
                "title": r["title"],
                "content": r.get("content"),
                "query": r["query"],
            }
            for r in unique
        ]
