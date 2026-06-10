"""Shared types for the result store / search history system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class ResultStoreEntry:
    """A single result set that a tool wants stored for later retrieval.

    Tools return a list of these from ``get_result_entries`` to declare
    what should be cached in the result store and search history.
    """

    items: List[Any]
    """Raw payload to store (groups for roon_search, result dicts for searxng)."""

    description: str
    """Factual label for the search history, e.g. '"Kate Bush"'."""

    item_count: int
    """Number of items for search history display."""

    tool_name: str = ""
    """Tool that produced this entry (for search history attribution)."""

    session_key: Optional[str] = None
    """Browse session key for drill-down routing."""

    is_drill_down: bool = False
    """If True, update an existing history entry instead of creating a new one."""
