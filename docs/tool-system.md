# Tool System

How Swarpius's tools and SKILL.md files work, and how to add new tools.

## Overview

Swarpius uses **native LLM tool calling** — tools are registered as Pydantic models with async execute methods, and the LLM decides which to call based on their schemas and descriptions. There is no framework (no Atomic Agents, no LangChain), just LiteLLM for the provider interface.

Each tool has two documentation layers:

1. **Pydantic input schema** — field names, types, constraints, and descriptions. Serialised into JSON Schema and sent to the LLM as the tool definition. This is the primary way the LLM understands what parameters a tool accepts.

2. **SKILL.md file** — domain-specific guidance that the schema can't convey: when to use the tool, strategic tips, output interpretation notes. Loaded at startup and included in the system prompt.

## Tool registration

Tools are registered in `RuntimeState.initialise()` via the `ToolRegistry`:

```python
self.tool_registry.register(
    "roon_search",                          # name (matches SKILL.md folder)
    "Search and browse the Roon music library",  # description for LLM schema
    RoonSearchToolInputSchema,              # Pydantic input model
    roon_search_tool.run_async,             # async executor
    tool_instance=roon_search_tool,         # back-reference for introspection
    display_label="Searching library",      # human-friendly label for frontend
)
```

The registry generates OpenAI-style tool schemas (`{"type": "function", "function": {...}}`) from the Pydantic models for the LLM API. It also handles dispatching: deserialising the LLM's JSON arguments into the input model, calling the executor, and returning the output.

## SKILL.md files

Each tool has a folder under `agent/skills/` containing a `SKILL.md` file:

```
agent/skills/
  roon-search/SKILL.md
  roon-action/SKILL.md
  roon-status/SKILL.md
  roon-config/SKILL.md
  result-fetch/SKILL.md
  web-search/SKILL.md
```

### Format

```markdown
---
name: roon-search
description: >
  One-paragraph description covering: what the tool does, when to use it
  (trigger conditions), when to avoid it (and what to use instead), and
  any key strategic tips. Aim for 100-200 tokens. Use the runtime skill
  ID (underscores) when referring to this skill.
  Runtime skill id: roon_search.
requires_tool: roon_search   # optional — drop skill if tool isn't registered
# requires_env: SOME_VAR     # optional — drop skill if env var is unset
---

## Execution guidance

Strategic tips that the Pydantic schema can't convey:
- Multi-step usage patterns
- Interactions with other tools
- Known limitations
- Best practices

<!-- critical -->
- High-priority directives that must not be lost mid-document.
  Extracted into a separate "Key Rules" context provider.
<!-- /critical -->

## Output notes

How to interpret the tool's output. Only needed if non-obvious.
```

### What goes where

| Information | Where it belongs | Why |
|---|---|---|
| Parameter names, types, constraints | Pydantic schema field descriptions | The LLM sees these directly via the tool definition |
| When to use / avoid | SKILL.md frontmatter `description` | Always in the system prompt; guides tool selection |
| Strategic usage patterns | SKILL.md body (Execution guidance) | Domain knowledge the schema can't express |
| Output interpretation | SKILL.md body (Output notes) | Only if the output has non-obvious semantics |

**Do not** duplicate parameter documentation in SKILL.md — the schema already provides this.

### Loading

All SKILL.md files are loaded statically at startup by the skill loader in `app/coordinator/skill_loader.py`. The frontmatter descriptions and trimmed bodies are formatted into two separate context providers: a main **skills** block (`<skill>` entries with name + description + instructions for each tool) and a **key-rules** block extracted from any `<!-- critical -->` directives (see "Critical directives" below). Both are always available to Swarpius. There is no dynamic loading or active skill tracking.

Two frontmatter fields control whether a skill makes it into the prompt at all:

- `requires_env: <ENV_VAR>` — drop the skill if the named env var is unset or empty.
- `requires_tool: <tool_name>` — drop the skill if the named tool isn't registered. Stricter than `requires_env`: a tool factory may decline to build the tool even when its credential is set (e.g. `WEB_SEARCH_PROVIDER` mismatch). The registry is the source of truth.

### Critical directives

Within a SKILL.md body, wrap high-priority instructions in `<!-- critical -->` … `<!-- /critical -->` markers. The skill loader extracts these into a separate "Key Rules" context provider that's positioned for recency bias in the system prompt. Useful for tuning smaller models that lose mid-document directives. See [`model-profiles.md`](model-profiles.md#critical-directive-markers) for the full description; the marker syntax is otherwise documented at the implementation site (`extract_critical_directives` in `app/coordinator/skill_loader.py`).

### Why static loading works

With 6 tools, all SKILL.md content totals roughly 1,500 tokens — trivial relative to modern context windows. Static loading eliminates the complexity of predicting which tool the LLM will use next, avoids timing issues with when docs are available, and lets Swarpius plan freely with full knowledge of all tools.

## How to add a new tool

### Step 1: Implement the tool

Create the tool module in `agent/tools/`:

```python
# agent/tools/my_tool.py

from pydantic import BaseModel, Field

class MyToolInputSchema(BaseModel):
    """Input for my_tool."""
    query: str = Field(description="What to search for")
    limit: int = Field(default=10, description="Maximum results to return")

class MyToolOutputSchema(BaseModel):
    """Output from my_tool."""
    results: list[str]
    total: int

class MyTool:
    def __init__(self, some_dependency):
        self.dep = some_dependency

    async def run_async(self, params: MyToolInputSchema) -> MyToolOutputSchema:
        # Do the work
        results = await self.dep.search(params.query, params.limit)
        return MyToolOutputSchema(results=results, total=len(results))
```

Field descriptions on the input schema matter — they are serialised into the JSON Schema that the LLM sees. Write them as though they are the only documentation the LLM will read for parameter construction (they usually are).

#### Optional methods

Tools can implement additional methods that the registry and tool loop call when present:

**`compact_output(output, handles=None) -> str`** — Format tool output as a compact string for the LLM context window. If `handles` are provided (result store handles allocated by the runtime), embed them as `[Result handle: ...]` markers so the LLM can reference results later. Without this method, the output's default JSON serialisation is used. See `roon_search.py` for an example.

**`get_result_entries(params, output) -> Optional[List[ResultStoreEntry]]`** — Declare what should be stored in the result store. Return a list of `ResultStoreEntry` objects for results that should be cached with handles (e.g. search results, library listings). The runtime allocates handles and passes them to `compact_output`. Return `None` if nothing should be stored. See `tools/web_search/base.py` (the shared base class implementing `get_result_entries` for all web-search providers) for an example.

**`compact_trace(output) -> Optional[dict]`** — Format tool output for the execution trace (used in logging and cross-request context). Return a dict summary, or `None` for default JSON serialisation. Useful for tools with large output that should be summarised. See `roon_status.py` for an example.

#### Class attributes

**`parallel_safe = True|False`** — Declares whether this tool is safe to run concurrently with other `parallel_safe` tools in the same step. When `PARALLEL_TOOLS` is enabled, the tool loop batches and parallelises safe calls via `asyncio.gather`. Default is `False` (sequential execution). Set to `True` for tools that don't share mutable state — typically read-only tools like search and status.

### Step 2: Create the SKILL.md

Create `agent/skills/my-tool/SKILL.md` using the format described above. The folder name uses hyphens; the runtime skill ID (referenced in the description) uses underscores.

Guidelines for the description:
- Be specific about trigger conditions — what user phrases should cause this tool to be selected
- Be explicit about what to avoid — name the alternative tools
- Include strategic tips that affect tool *selection* (not just usage)

### Step 3: Register in RuntimeState

In `agent/app/runtime/state.py`, in the `initialise()` method:

```python
my_tool = MyTool(some_dependency)

self.tool_registry.register(
    "my_tool",
    "Short description for the LLM tool schema",
    MyToolInputSchema,
    my_tool.run_async,
    tool_instance=my_tool,
    display_label="Doing my thing",
)
```

The skill loader auto-discovers the SKILL.md by matching the folder name (`my-tool`) to the registered tool name (`my_tool`, with hyphens replaced by underscores).

### Step 4: Test

1. Run existing tests to verify nothing broke: `python3 -m pytest`
2. The skill loader will auto-discover the new SKILL.md and include it in Swarpius's prompt
3. Add unit tests for the tool in `agent/tests/`
4. Test manually with requests that should trigger the new tool

### Step 5: Lint

```bash
ruff check .
```

## Removing a tool

1. Delete the `agent/skills/my-tool/` directory
2. Remove the `tool_registry.register()` call from `RuntimeState.initialise()`
3. Delete the tool module from `agent/tools/`
4. Remove or update any tests that reference the tool

## Result store and handles

Tools that produce query results (search results, library listings, web search results) can declare their output as storable via `get_result_entries()`. This enables:

1. **Pagination** — the `result_fetch` tool retrieves cached results by handle, so the LLM can page through large result sets without re-searching
2. **Inline annotation** — `compact_output` embeds `[Result handle: res_NNNNN]` markers in the LLM context, teaching it how to reference results
3. **Cross-request context** — the search history context provider lists active handles with descriptions, so the LLM knows what's available from previous searches

When a tool's `get_result_entries` returns entries, the runtime:
1. Stores each entry's items in the in-memory result store
2. Allocates a handle (format: `res_NNNNN`)
3. Passes the handles to the tool's `compact_output` for inline annotation

Currently `roon_search` and `web_search` use this system.

## Design rationale

### Why no framework?

Swarpius uses native tool calling rather than an agent framework because:

- **Simpler mental model**: the LLM decides what to do, tools execute, results go back into the conversation. No intent router, no planning step, no output schema validation.
- **Provider flexibility**: native tool calling works identically across all LiteLLM-supported providers. Framework-specific features (like instructor-style output schema enforcement) don't always translate across providers.
- **Fewer moving parts**: a couple hundred lines of registry + loop replace the routing, validation, and retry layers a typical framework introduces.

### Why keep SKILL.md files?

Given that all content is loaded statically, everything could theoretically go in the system prompt. SKILL.md files are kept for **separation of concerns**:

- Adding a tool means creating one folder with one file. No edits to the system prompt.
- "What does Swarpius know about roon_search?" has one answer: look at `skills/roon-search/SKILL.md`.
- Tool documentation and orchestration logic are tested independently.
- If the orchestration approach changes, the SKILL.md files remain useful — they document tool behaviour independently of how tools are called.
