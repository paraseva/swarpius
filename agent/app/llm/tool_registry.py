"""Tool registry: maps tool names to executors and generates LLM tool schemas.

Each tool is registered with a name, description, Pydantic input model,
and an async execute function.  The registry can:

- Generate native tool-calling schemas for the LLM API (via LiteLLM)
- Dispatch a tool call by name, deserialising the arguments into the
  Pydantic input model and calling the executor
- List available tools for prompt injection
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError

_log = logging.getLogger("swarpius.tool_registry")


@dataclass
class RegisteredTool:
    """A single registered tool."""

    name: str
    description: str
    input_schema: Type[BaseModel]
    execute: Callable[[BaseModel], Awaitable[BaseModel]]
    tool_instance: Any = None  # optional back-reference to the tool object
    display_label: str = ""  # human-friendly label for frontend display
    parallel_safe: bool = False  # can this tool run concurrently with others?


def _pydantic_to_json_schema(model: Type[BaseModel]) -> dict:
    """Convert a Pydantic model to a JSON Schema dict suitable for
    the ``parameters`` field of an OpenAI-style function definition.

    Strips Pydantic-specific keys (``title`` at top level) and ensures
    the schema is self-contained (``$defs`` inlined).
    """
    schema = model.model_json_schema()
    # Remove top-level title — the function name serves that purpose
    schema.pop("title", None)
    return schema


class ToolRegistry:
    """Maintains a set of tools and produces LLM-compatible schemas."""

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: Type[BaseModel],
        execute: Callable[[BaseModel], Awaitable[BaseModel]],
        tool_instance: Any = None,
        display_label: str = "",
        parallel_safe: bool = False,
    ) -> None:
        if name in self._tools:
            _log.warning("Overwriting already-registered tool %r", name)
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            input_schema=input_schema,
            execute=execute,
            tool_instance=tool_instance,
            display_label=display_label,
            parallel_safe=parallel_safe,
        )

    def get(self, name: str) -> Optional[RegisteredTool]:
        return self._tools.get(name)

    def is_parallel_safe(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool.parallel_safe if tool else False

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools)

    def to_tool_schemas(self) -> List[dict]:
        """Return OpenAI-style ``tools`` list for the LLM API.

        Each entry is ``{"type": "function", "function": {...}}``.
        """
        schemas: List[dict] = []
        for tool in self._tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": _pydantic_to_json_schema(tool.input_schema),
                },
            })
        return schemas

    def compact_output(
        self,
        tool_name: str,
        output: Any,
        handles: Optional[List[str]] = None,
    ) -> str:
        """Compact tool output for LLM context.

        Delegates to the tool instance's ``compact_output`` method if available.
        Falls back to ``model_dump_json()`` for Pydantic models or ``json.dumps``.
        When *handles* is provided, they are passed through so the tool can
        include ``[Result handle: ...]`` markers inline.
        """
        tool = self._tools.get(tool_name)
        if tool and tool.tool_instance and hasattr(tool.tool_instance, "compact_output"):
            result = tool.tool_instance.compact_output(output, handles=handles)
            if result is not None:
                return result
        if isinstance(output, BaseModel):
            return output.model_dump_json()
        return json.dumps(output)

    def get_result_entries(
        self,
        tool_name: str,
        arguments: dict,
        output: Any,
    ) -> Optional[List]:
        """Ask the tool what result entries it wants stored.

        Delegates to the tool instance's ``get_result_entries`` method.
        Parses *arguments* (raw dict) into the tool's input schema before
        passing to the tool.  Returns ``None`` if the tool doesn't produce
        storable results.
        """
        tool = self._tools.get(tool_name)
        if tool and tool.tool_instance and hasattr(tool.tool_instance, "get_result_entries"):
            try:
                parsed_input = tool.input_schema.model_validate(arguments)
            except ValidationError as exc:
                _log.warning(
                    "Tool %r: invalid arguments for result-entry extraction "
                    "(dropped). %s", tool_name, exc,
                )
                return None
            return tool.tool_instance.get_result_entries(parsed_input, output)
        return None

    def compact_trace(self, tool_name: str, output: Any) -> Optional[dict]:
        """Compact tool output for execution trace.

        Delegates to the tool instance's ``compact_trace`` method if available.
        Returns ``None`` when the caller should apply default compaction.
        """
        tool = self._tools.get(tool_name)
        if tool and tool.tool_instance and hasattr(tool.tool_instance, "compact_trace"):
            return tool.tool_instance.compact_trace(output)
        return None

    async def execute(self, tool_name: str, arguments: dict) -> BaseModel:
        """Dispatch a tool call: deserialise arguments → execute → return output."""
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Unknown tool: {tool_name!r}")
        try:
            parsed_input = tool.input_schema.model_validate(arguments)
        except Exception as exc:
            raise ValueError(
                f"Invalid arguments for tool {tool_name!r}: {exc}"
            ) from exc
        return await tool.execute(parsed_input)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
