from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.roon.compact_formatting import _compact_items


class ResultFetchToolInputSchema(BaseModel):
    """Input schema for fetching cached list items by result handle."""

    result_handle: str = Field(
        ...,
        description="Handle from search_history (format: res_NNNNN).",
    )


class ResultFetchToolOutputSchema(BaseModel):
    """Output schema containing fetched list content."""

    result_handle: str = Field(..., description="Requested handle")
    result: str = Field(..., description="Outcome description")
    items: List[Any] = Field(default_factory=list, description="Fetched list items")
    total_count: int = Field(0, description="Total item count in cached list")
    error: Optional[str] = Field(None, description="Error description when retrieval fails")


class ResultFetchToolConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    result_store: Optional[Any] = None
    search_history: Optional[Any] = None


class ResultFetchTool:
    input_schema = ResultFetchToolInputSchema
    output_schema = ResultFetchToolOutputSchema
    parallel_safe = True

    def __init__(self, config: ResultFetchToolConfig = ResultFetchToolConfig()) -> None:
        self.config = config
        # Keep a shared store reference even when it starts empty.
        if config.result_store is None:
            self.result_store = {}
        elif isinstance(config.result_store, dict):
            self.result_store = config.result_store
        else:
            raise TypeError("result_store must be a dict when provided")
        self.search_history: list = config.search_history if isinstance(config.search_history, list) else []

    def compact_trace(self, output: "ResultFetchToolOutputSchema") -> dict:
        """Keep full output for trace (items are already compact)."""
        return output.model_dump(mode="json")

    def _describe_handle(self, handle: str) -> str:
        """Look up the search history description for a result handle."""
        for entry in self.search_history:
            if getattr(entry, "result_handle", None) == handle:
                return getattr(entry, "description", "")
        return ""

    async def run_async(self, params: ResultFetchToolInputSchema) -> ResultFetchToolOutputSchema:
        payload = self.result_store.get(params.result_handle)
        if payload is None:
            return ResultFetchToolOutputSchema(
                result_handle=params.result_handle,
                result="Result handle not found",
                error=(
                    f"Unknown result_handle '{params.result_handle}'. "
                    "It may have expired or was never created in this runtime."
                ),
            )
        if not isinstance(payload, list):
            return ResultFetchToolOutputSchema(
                result_handle=params.result_handle,
                result="Result handle does not reference a list",
                error=f"Handle '{params.result_handle}' points to type '{type(payload).__name__}'",
            )

        desc = self._describe_handle(params.result_handle)
        label = f"List for: {desc}" if desc else "Cached list retrieved"

        return ResultFetchToolOutputSchema(
            result_handle=params.result_handle,
            result=label,
            items=_compact_items(payload),
            total_count=len(payload),
        )

    def run(self, params: ResultFetchToolInputSchema) -> ResultFetchToolOutputSchema:
        with ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, self.run_async(params)).result()
