"""Web search tools — provider-neutral base + per-provider implementations.

Each provider extends :class:`WebSearchTool` and implements
``_fetch_search_results`` (per-query API call) plus optionally overrides
``_post_process`` for provider-specific cross-query massaging (score
sorting, title augmentation, etc.). Shared logic — fan-out, dedup,
max_results limiting, schema construction, LLM-context formatting, and
``ResultStoreEntry`` derivation — lives in the base class.
"""

from tools.web_search.base import (
    WebSearchResultItemSchema,
    WebSearchTool,
    WebSearchToolConfig,
    WebSearchToolInputSchema,
    WebSearchToolOutputSchema,
)
from tools.web_search.brave import (
    BraveSearchTool,
    BraveSearchToolConfig,
)
from tools.web_search.searxng import (
    SearXNGSearchTool,
    SearXNGSearchToolConfig,
)
from tools.web_search.tavily import (
    TavilySearchTool,
    TavilySearchToolConfig,
)

__all__ = [
    "BraveSearchTool",
    "BraveSearchToolConfig",
    "SearXNGSearchTool",
    "SearXNGSearchToolConfig",
    "TavilySearchTool",
    "TavilySearchToolConfig",
    "WebSearchResultItemSchema",
    "WebSearchTool",
    "WebSearchToolConfig",
    "WebSearchToolInputSchema",
    "WebSearchToolOutputSchema",
]
