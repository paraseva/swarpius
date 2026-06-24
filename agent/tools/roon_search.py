from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exceptions import UnsupportedActionError
from app.roon.compact_formatting import _compact_items
from app.runtime.result_store_types import ResultStoreEntry
from roon_core.browse_session import SearchRecipe
from roon_core.schemas import RoonCoreResultsGroupSchema, RoonCoreResultsSchema

RoonSearchOperation = Literal["new_search", "drill_down_reference"]


class RoonSearchToolInputSchema(BaseModel):
    """
    One-step browsing interface for the Roon search hierarchy.
    """

    operation: RoonSearchOperation = Field(
        ...,
        description="Type of search or browse operation to perform",
    )
    search_string: Optional[str] = Field(
        None,
        description="Search text for a fresh search (required for new_search)",
    )
    reference: Optional[str] = Field(
        None,
        description="Reference from a previous result to drill down one level",
    )
    @model_validator(mode="after")
    def validate_operation_requirements(self) -> "RoonSearchToolInputSchema":
        if self.operation == "new_search" and not self.search_string:
            raise ValueError("search_string is required when operation is new_search")
        if self.operation == "drill_down_reference" and not self.reference:
            raise ValueError("reference is required when operation is drill_down_reference")
        return self


class RoonSearchToolOutputSchema(BaseModel):
    """Output of a one-step Roon browse/search operation."""

    description: str = Field(
        ...,
        description="Description of operation outcome",
    )
    groups: List[RoonCoreResultsGroupSchema] = Field(
        ...,
        description="Result groups — each group has a label and a list of items",
    )
    session_key: Optional[str] = Field(
        None,
        description="Browse session key that produced these results",
    )
    search_attempts: int = Field(1, exclude=True)
    search_retry_notes: Optional[List[str]] = Field(None, exclude=True)


class RoonSearchToolConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    roon_connection: Optional[Any] = None


class RoonSearchTool:
    """
    Tool for one-level-at-a-time searching and browsing in Roon.
    """

    input_schema = RoonSearchToolInputSchema
    output_schema = RoonSearchToolOutputSchema
    parallel_safe = True

    def __init__(self, config: RoonSearchToolConfig = RoonSearchToolConfig()) -> None:
        self.config = config
        self.roon_connection = config.roon_connection

    def compact_output(
        self,
        output: RoonSearchToolOutputSchema,
        handles: Optional[List[str]] = None,
    ) -> str:
        raw = output.model_dump(mode="json")
        description = raw.get("description", "").rstrip(".")
        groups = raw.get("groups", [])
        items = _compact_items(groups)
        lines = [f"{description}. {len(items)} results."]
        lines.extend(str(item) for item in items)
        text = "\n".join(lines)
        if handles:
            prefix = "\n".join(f"[Result handle: {h}]" for h in handles)
            return f"{prefix}\n{text}"
        return text

    def compact_trace(self, output: RoonSearchToolOutputSchema) -> dict:
        raw = output.model_dump(mode="json")
        groups = raw.get("groups", [])
        return {
            "description": raw.get("description", ""),
            "items": _compact_items(groups),
        }

    def get_result_entries(
        self,
        params: RoonSearchToolInputSchema,
        output: RoonSearchToolOutputSchema,
    ) -> Optional[List[ResultStoreEntry]]:
        """Declare what should be stored in the result store."""
        raw = output.model_dump(mode="json")
        items = raw.get("groups", [])
        if not items:
            return None

        operation = params.operation
        session_key = getattr(output, "session_key", None)
        is_drill = operation == "drill_down_reference"

        if is_drill:
            description = f"drill_down ref {params.reference or '?'}"
        else:
            description = f'"{params.search_string or "browse"}"'

        item_count = sum(
            len(g.get("items", [])) if isinstance(g, dict) and "items" in g else 1
            for g in items
        )

        return [ResultStoreEntry(
            items=items,
            description=description,
            item_count=item_count,
            tool_name="roon_search",
            session_key=session_key,
            is_drill_down=is_drill,
        )]

    def _output(
        self,
        description: str,
        recipe: Optional[SearchRecipe] = None,
        session_key: Optional[str] = None,
    ) -> RoonSearchToolOutputSchema:
        return RoonSearchToolOutputSchema(
            description=description,
            groups=self.roon_connection.compile_output(
                recipe=recipe, session_key=session_key,
            ),
        )

    async def run_async(self, params: RoonSearchToolInputSchema) -> RoonSearchToolOutputSchema:
        if params.operation == "new_search":
            return self._new_search(params)
        if params.operation == "drill_down_reference":
            return self._drill_down_reference(params)
        raise UnsupportedActionError(f"Unknown operation '{params.operation}'")

    def _finish(
        self,
        description: str,
        recipe: Optional[SearchRecipe],
        session_key: str,
        current_list: Optional[RoonCoreResultsSchema],
    ) -> RoonSearchToolOutputSchema:
        if not current_list:
            raise ValueError("No results returned from Roon search operation")
        output = self._output(description, recipe=recipe, session_key=session_key)
        output.session_key = session_key
        output.search_attempts = current_list.search_attempts
        output.search_retry_notes = current_list.search_retry_notes
        return output

    def _new_search(self, params: RoonSearchToolInputSchema) -> RoonSearchToolOutputSchema:
        session_key = self.roon_connection.session_manager.new_search_session()
        current_list = self.roon_connection.browse_core(
            {"pop_all": True, "input": params.search_string},
            session_key=session_key,
        )
        return self._finish(
            f"Search results for '{params.search_string}'.",
            SearchRecipe(search_string=params.search_string),
            session_key,
            current_list,
        )

    def _drill_down_reference(
        self, params: RoonSearchToolInputSchema,
    ) -> RoonSearchToolOutputSchema:
        conn = self.roon_connection
        manager = conn.session_manager
        existing = manager.get_ref(params.reference)
        if not existing:
            raise LookupError(
                f"Reference '{params.reference}' not found — check that it "
                "matches a reference from the search results exactly.",
            )

        # Reserve the reference's session for this drill. If a concurrent
        # operation already holds it, ``acquire`` leases a fresh session and we
        # re-establish the reference there — so sibling drills never share one
        # Roon browse cursor. ``release`` in finally returns it to the pool.
        requested = existing.roon_session_key
        granted = manager.acquire(requested)
        try:
            target_session = None if granted == requested else granted
            ref = conn.resolve_reference(params.reference, target_session=target_session)
            if not ref or not ref.cached_item_key:
                raise LookupError(
                    f"Reference '{params.reference}' has expired. "
                    "Run a fresh search to get new references.",
                )
            recipe = SearchRecipe(
                search_string=ref.recipe.search_string,
                category=ref.recipe.category,
                parent_chain=list(ref.recipe.parent_chain) + [ref.identity],
            )
            current_list = conn.drill_down(
                drilldown_item=self._temp_item(ref),
                session_key=ref.roon_session_key,
            )
            return self._finish(
                f"Drilled down one level for reference '{params.reference}'.",
                recipe,
                ref.roon_session_key,
                current_list,
            )
        finally:
            manager.release(granted)

    @staticmethod
    def _temp_item(ref):
        from roon_core.schemas import RoonCoreItemSchema

        return RoonCoreItemSchema(
            title=ref.identity.title,
            subtitle=ref.identity.subtitle,
            item_key=ref.cached_item_key,
            hint=ref.identity.hint,
            image_key=ref.identity.image_key,
            item_key_path=list(ref.item_key_path),
        )

    def run(self, params: RoonSearchToolInputSchema) -> RoonSearchToolOutputSchema:
        with ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, self.run_async(params)).result()
