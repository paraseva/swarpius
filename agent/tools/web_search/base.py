"""Provider-neutral base class and schemas for web-search tools.

The base handles fan-out across queries, dedup-by-URL, max_results
limiting, schema construction, and LLM-context formatting. Providers
implement :meth:`WebSearchTool._fetch_search_results` (per-query HTTP
call) returning raw provider dicts, and may override
:meth:`WebSearchTool._post_process` to do cross-query sorting,
filtering, or normalisation before dedup.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Literal, Optional

import aiohttp
from pydantic import BaseModel, Field

from app.exceptions import ExternalServiceError
from app.runtime.result_store_types import ResultStoreEntry


class WebSearchToolInputSchema(BaseModel):
    """
    Schema for input to a tool for searching the public web for facts,
    references, and other content.

    Returns a list of search results with a short description or content
    snippet and URLs for further exploration.
    """

    queries: List[str] = Field(..., description="List of search queries.")
    category: Optional[Literal["general", "news", "social_media"]] = Field(
        "general",
        description="Category of the search queries.",
    )


class WebSearchResultItemSchema(BaseModel):
    """A single web-search result item."""

    url: str = Field(..., description="The URL of the search result")
    title: str = Field(..., description="The title of the search result")
    content: Optional[str] = Field(
        None,
        description="The content snippet of the search result",
    )
    query: str = Field(
        ...,
        description="The query used to obtain this search result",
    )


class WebSearchToolOutputSchema(BaseModel):
    """Output schema for the web search tool."""

    results: List[WebSearchResultItemSchema] = Field(
        ...,
        description="List of search result items",
    )
    category: Optional[str] = Field(
        None,
        description="The category of the search results",
    )


class WebSearchToolConfig(BaseModel):
    max_results: int = 10


class WebSearchTool:
    """Provider-neutral base class for web-search tools."""

    input_schema = WebSearchToolInputSchema
    output_schema = WebSearchToolOutputSchema
    parallel_safe = True
    provider_name: str = "unknown"

    def __init__(self, config: WebSearchToolConfig):
        self.config = config
        self.max_results = config.max_results

    async def _fetch_search_results(
        self,
        session: aiohttp.ClientSession,
        query: str,
        category: Optional[str],
    ) -> List[dict]:
        """Per-query fetch. Returns raw provider dicts; the canonical
        ``{url, title, content, query}`` shape is enforced after
        :meth:`_post_process` runs."""
        raise NotImplementedError

    def _post_process(
        self,
        flat: List[dict],
        category: Optional[str],
    ) -> List[dict]:
        """Cross-query post-processing hook. Default: identity (the
        provider's ``_fetch_search_results`` already produces canonical
        dicts). Override to sort, filter, or normalise before dedup."""
        return flat

    @staticmethod
    def _wrap_text(text: str, first_prefix: str, continuation_indent: str, width: int) -> str:
        """Wrap text with a prefix on the first line and indentation on continuations.

        Words longer than the available line width are force-broken at the margin.
        """
        available = width - len(first_prefix)
        if available <= 0 or len(text) <= available:
            return f"{first_prefix}{text}"

        cont_available = width - len(continuation_indent)
        if cont_available <= 0:
            return f"{first_prefix}{text}"

        words = text.split()
        if not words:
            return first_prefix

        result_lines: list[str] = []
        current_line = first_prefix

        for word in words:
            is_start = current_line in (first_prefix, continuation_indent)
            space = "" if is_start else " "

            if len(current_line) + len(space) + len(word) <= width:
                current_line += space + word
                continue

            if not is_start:
                result_lines.append(current_line)
                current_line = continuation_indent

            if len(word) <= cont_available:
                current_line += word
                continue

            # Force-break: word exceeds a full line
            while word:
                space_left = width - len(current_line)
                current_line += word[:space_left]
                word = word[space_left:]
                if word:
                    result_lines.append(current_line)
                    current_line = continuation_indent

        if current_line:
            result_lines.append(current_line)

        return "\n".join(result_lines)

    def _format_output(self, output: WebSearchToolOutputSchema) -> str:
        raw = output.model_dump(mode="json")
        results = raw.get("results") or []
        category = raw.get("category") or "general"

        # Group results by query, preserving order of first appearance
        groups: list[tuple[str, list[dict]]] = []
        seen_queries: dict[str, int] = {}
        for item in results:
            q = item.get("query", "web search")
            if q not in seen_queries:
                seen_queries[q] = len(groups)
                groups.append((q, []))
            groups[seen_queries[q]][1].append(item)

        if not groups:
            return "Web search results for 'web search' (category: general). 0 results."

        blocks: list[str] = []
        for query, items in groups:
            lines = [f"Web search results for '{query}' (category: {category}). {len(items)} results."]
            for i, item in enumerate(items, 1):
                title = item.get("title", "")
                url = item.get("url", "")
                content = item.get("content") or ""

                num = f"({i}) "
                pad = " " * len(num)

                prefix = f"{num}title:   "
                lines.append(self._wrap_text(title, prefix, " " * len(prefix), width=100))
                prefix = f"{pad}url:     "
                lines.append(self._wrap_text(url, prefix, " " * len(prefix), width=100))
                if content:
                    prefix = f"{pad}content: "
                    lines.append(self._wrap_text(content, prefix, " " * len(prefix), width=100))
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    def compact_output(
        self,
        output: WebSearchToolOutputSchema,
        handles: Optional[List[str]] = None,
    ) -> str:
        text = self._format_output(output)
        if not handles:
            return text
        groups = text.split("\n\n")
        parts = []
        for i, group in enumerate(groups):
            handle = handles[i] if i < len(handles) else None
            if handle:
                parts.append(f"[Result handle: {handle}]\n{group}")
            else:
                parts.append(group)
        return "\n\n".join(parts)

    def get_result_entries(
        self,
        params: WebSearchToolInputSchema,
        output: WebSearchToolOutputSchema,
    ) -> Optional[List[ResultStoreEntry]]:
        """Declare what should be stored — one entry per query group."""
        raw = output.model_dump(mode="json")
        results = raw.get("results") or []
        if not results:
            return None

        # Group results by query, preserving order
        groups: dict[str, list] = {}
        for item in results:
            q = item.get("query", "web search") if isinstance(item, dict) else "web search"
            groups.setdefault(q, []).append(item)

        entries = []
        for query, items in groups.items():
            entries.append(ResultStoreEntry(
                items=items,
                description=f'"{query}"',
                item_count=len(items),
                tool_name="web_search",
            ))
        return entries

    def _mark_backend_unreachable(self, exc: Exception) -> None:
        """Flag the web-search backend down in the live validation status
        when a search fails at use-time, so the Settings highlight reflects
        it. Recovery is via the health loop (SearXNG) or the Test button."""
        from app.settings.backends import backend_for_provider
        mapping = backend_for_provider(self.provider_name)
        if mapping is None:
            return
        backend_id, label = mapping
        from app.settings.validation import BackendResult, get_validator
        get_validator().update_backend(BackendResult(
            backend=backend_id, label=label, ok=False,
            error_kind="other", detail=f"Search failed: {exc}",
        ))

    async def run_async(
        self,
        params: WebSearchToolInputSchema,
        max_results: Optional[int] = None,
    ) -> WebSearchToolOutputSchema:
        try:
            async with aiohttp.ClientSession() as session:
                tasks = [
                    self._fetch_search_results(session, query, params.category)
                    for query in params.queries
                ]
                per_query = await asyncio.gather(*tasks)
        except ExternalServiceError as exc:
            self._mark_backend_unreachable(exc)
            raise

        flat = [item for sublist in per_query for item in sublist]
        flat = self._post_process(flat, params.category)

        # Dedup by URL, preserving order
        seen_urls: set[str] = set()
        unique: list[dict] = []
        for r in flat:
            url = r.get("url")
            if not url or url in seen_urls:
                continue
            unique.append(r)
            seen_urls.add(url)

        limit = max_results or self.max_results
        unique = unique[:limit]

        return WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    url=r["url"],
                    title=r["title"],
                    content=r.get("content"),
                    query=r["query"],
                )
                for r in unique
            ],
            category=params.category,
        )

    def run(
        self,
        params: WebSearchToolInputSchema,
        max_results: Optional[int] = None,
    ) -> WebSearchToolOutputSchema:
        with ThreadPoolExecutor() as executor:
            return executor.submit(
                asyncio.run,
                self.run_async(params, max_results),
            ).result()
