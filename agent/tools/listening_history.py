from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ListeningHistoryToolInputSchema(BaseModel):
    """Input schema for querying the listening history."""

    since: Optional[str] = Field(
        None,
        description=(
            "Start of the range (inclusive), ISO 8601 — a date like "
            "'2026-06-15' or a datetime like '2026-06-15T18:00'. Omit for no "
            "lower bound. Use today's date from the prompt to resolve relative "
            "ranges such as 'last Tuesday' or 'this morning'."
        ),
    )
    until: Optional[str] = Field(
        None,
        description=(
            "End of the range (inclusive), ISO 8601. A bare date covers that "
            "whole day. Omit for no upper bound."
        ),
    )
    zone: Optional[str] = Field(
        None,
        description="Limit to one zone by its display name. Omit for all zones.",
    )
    limit: int = Field(
        100,
        description="Maximum number of tracks to return, most recent first.",
    )


class ListeningPlay(BaseModel):
    when: str = Field(..., description="When it played (local time)")
    zone: Optional[str] = Field(None, description="Zone it played in")
    title: str = Field(..., description="Track title")
    artist: Optional[str] = Field(None, description="Artist")
    album: Optional[str] = Field(None, description="Album")


class ListeningHistoryToolOutputSchema(BaseModel):
    result: str = Field(..., description="Outcome description")
    plays: List[ListeningPlay] = Field(
        default_factory=list, description="Matching plays, most recent first",
    )
    count: int = Field(0, description="Number of plays returned")
    error: Optional[str] = Field(None, description="Error description when the query fails")


class ListeningHistoryToolConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    get_listening_history: Optional[Callable[[], Any]] = None


def _to_ms(value: str, *, end_of_day: bool) -> int:
    dt = datetime.fromisoformat(value)
    # A bare date as an upper bound should cover the whole day.
    if end_of_day and len(value) == 10:
        dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.timestamp() * 1000)


def _format_when(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")


class ListeningHistoryTool:
    input_schema = ListeningHistoryToolInputSchema
    output_schema = ListeningHistoryToolOutputSchema
    parallel_safe = True

    def __init__(self, config: ListeningHistoryToolConfig = ListeningHistoryToolConfig()) -> None:
        self.config = config

    def compact_trace(self, output: "ListeningHistoryToolOutputSchema") -> dict:
        return output.model_dump(mode="json")

    async def run_async(
        self, params: ListeningHistoryToolInputSchema,
    ) -> ListeningHistoryToolOutputSchema:
        store = (
            self.config.get_listening_history()
            if self.config.get_listening_history else None
        )
        if store is None:
            return ListeningHistoryToolOutputSchema(
                result="Listening history unavailable",
                error="Listening history is not available in this runtime.",
            )
        try:
            since_ms = _to_ms(params.since, end_of_day=False) if params.since else None
            until_ms = _to_ms(params.until, end_of_day=True) if params.until else None
        except ValueError as exc:
            return ListeningHistoryToolOutputSchema(
                result="Invalid date range",
                error=f"Could not parse a date in the request: {exc}",
            )

        rows = store.query(
            since_ms=since_ms,
            until_ms=until_ms,
            zone=params.zone,
            limit=max(1, params.limit),
        )
        plays = [
            ListeningPlay(
                when=_format_when(row["ts"]),
                zone=row.get("zone"),
                title=row["title"],
                artist=row.get("artist"),
                album=row.get("album"),
            )
            for row in rows
        ]
        return ListeningHistoryToolOutputSchema(
            result=f"{len(plays)} track(s) found" if plays else "No matching plays",
            plays=plays,
            count=len(plays),
        )

    def run(self, params: ListeningHistoryToolInputSchema) -> ListeningHistoryToolOutputSchema:
        with ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, self.run_async(params)).result()
