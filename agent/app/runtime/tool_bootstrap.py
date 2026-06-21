"""Tool instantiation + registry construction.

Builds every tool the agent comes with — Roon search/action/status/
config, result_fetch, optional web_search — and registers each with
the runtime's :class:`ToolRegistry`. Extracted from
``_StateInitMixin._setup_tool_registry`` so a contributor adding a
new tool edits this one focused file.

Free function rather than a class because there's no state to own:
``tool_registry`` already exists separately, tools live in the
registry after construction. Same pattern as
:mod:`roon_core.image_fetch`.
"""

from __future__ import annotations

from typing import Optional

from app.runtime.state_helpers import _build_web_search_tool
from tools.listening_history import (
    ListeningHistoryTool,
    ListeningHistoryToolConfig,
    ListeningHistoryToolInputSchema,
)
from tools.result_fetch import ResultFetchTool, ResultFetchToolConfig, ResultFetchToolInputSchema
from tools.roon_action import RoonActionTool, RoonActionToolConfig, RoonActionToolInputSchema
from tools.roon_config import RoonConfigTool, RoonConfigToolConfig, RoonConfigToolInputSchema
from tools.roon_search import RoonSearchTool, RoonSearchToolConfig, RoonSearchToolInputSchema
from tools.roon_status import RoonStatusTool, RoonStatusToolConfig, RoonStatusToolInputSchema
from tools.web_search import WebSearchTool, WebSearchToolInputSchema


def register_runtime_tools(runtime, settings) -> Optional[WebSearchTool]:
    """Instantiate every tool and register it with the runtime's
    tool registry. Returns the web-search tool (or None) so the
    caller's startup summary can name the provider."""
    web_search_tool = _build_web_search_tool(settings)
    result_fetch_tool = ResultFetchTool(
        ResultFetchToolConfig(
            result_store=runtime.result_store,
            search_history=runtime.search_history,
        ),
    )
    listening_history_tool = ListeningHistoryTool(
        ListeningHistoryToolConfig(
            get_listening_history=lambda: runtime.listening_history,
        ),
    )
    roon_config_tool = RoonConfigTool(RoonConfigToolConfig(
        roon_connection=runtime.roon_connection,
        perform_config_action=runtime.perform_config_action,
    ))
    roon_search_tool = RoonSearchTool(
        RoonSearchToolConfig(roon_connection=runtime.roon_connection),
    )
    roon_action_tool = RoonActionTool(
        RoonActionToolConfig(
            roon_connection=runtime.roon_connection,
            resolve_zone=runtime.resolve_zone_name,
            result_store=runtime.result_store,
            shutdown_event=runtime.shutdown_event,
            stop_marker_coordinator_getter=(
                lambda: runtime.stop_marker_coordinator
            ),
        ),
    )
    roon_status_tool = RoonStatusTool(
        RoonStatusToolConfig(
            roon_connection=runtime.roon_connection,
            resolve_zone=runtime.resolve_zone_name,
            queue_display_cache=runtime.queue_display_cache,
            format_zone_label=runtime.format_zone_label,
            get_last_played_dict=runtime.play_history.get_last_played_dict,
            get_reverse_aliases=runtime.zone_domain.build_reverse_aliases,
            get_default_zone=runtime.roon_connection.get_default_zone,
        ),
    )

    tool_specs = [
        ("roon_search", "Search and browse the Roon music library",
         RoonSearchToolInputSchema, roon_search_tool, "Searching library"),
        ("roon_action", "Play, queue, or control playback in Roon",
         RoonActionToolInputSchema, roon_action_tool, "Controlling playback"),
        ("roon_status", "Get playback, queue, or zone status from Roon",
         RoonStatusToolInputSchema, roon_status_tool, "Checking zone status"),
        ("roon_config", "Configure Roon default zone, aliases, or zone transfer",
         RoonConfigToolInputSchema, roon_config_tool, "Updating configuration"),
        ("result_fetch", "Retrieve cached search results by result_handle",
         ResultFetchToolInputSchema, result_fetch_tool, "Fetching results"),
        ("listening_history", "Look up what was played and when (listening history)",
         ListeningHistoryToolInputSchema, listening_history_tool, "Checking listening history"),
    ]
    if web_search_tool is not None:
        tool_specs.append(
            ("web_search", "Search the public web for facts and references",
             WebSearchToolInputSchema, web_search_tool, "Searching the web"),
        )
    for name, description, schema, tool, label in tool_specs:
        runtime.tool_registry.register(
            name, description, schema, tool.run_async,
            tool_instance=tool,
            display_label=label,
            parallel_safe=getattr(tool, "parallel_safe", False),
        )

    return web_search_tool
