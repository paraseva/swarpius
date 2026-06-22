# Architecture

This document describes how Swarpius's agent backend works, from user message to response.

## Overview

Swarpius uses a **native tool-calling loop**. The LLM receives the full set of available tools on every call and decides whether to call a tool or produce a text response. The LLM plans and coordinates its activities iteratively.

The sole LLM dependency is [LiteLLM](https://docs.litellm.ai), which provides a unified interface to multiple providers (Anthropic, OpenAI, Ollama, etc.).

```
        User message
             │
             ▼
┌──────────────────────────┐
│  Conversation classifier │  (optional, lightweight LLM call)
│  assigns request to a    │
│  conversation thread     │
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│  System prompt assembly  │  static base prompt + dynamic context sections
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│     Tool-calling loop    │◄─── LLM call ──► tool execution ───┐
│                          │                                    │
│   Repeat until the LLM   │◄───────────────────────────────────┘
│   produces text or step  │
│   limit is reached       │
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│    Response extraction   │  sanitise, expand tags, emit via WS
└──────────────────────────┘
```

## Key modules

| Module | Role |
|---|---|
| `request_flow.py` | Orchestrates the full pipeline: prompt assembly, loop invocation, response emission, logging |
| `tool_loop.py` | The core loop: call LLM → execute tools → repeat |
| `tool_registry.py` | Maps tool names to executors, generates LLM-compatible schemas |
| `app/llm/client.py` | Thin LiteLLM wrapper with normalised response objects |
| `app/runtime/state.py` | Central state: initialisation, tool registration, context providers, zone management |
| `context_providers.py` | Dynamic context sections injected into the system prompt |
| `conversation_tracker.py` | Conversation thread management and assignment |
| `diagnostic_agent.py` | Optional LLM-driven conversation classification |

## Request flow in detail

The full pipeline is implemented in `request_flow.py`. Every user message goes through these stages:

### 1. Conversation classification
 
This feature is intended to facilitate better holistic conversation analyses by grouping discontiguous user requests that are semantically part of the same conversation. Conversations, indexed by `cXX`, are groups of requests denoted by the request ID format `rq-cXX-YYYY` (e.g. `rq-c01-0003`, denotes request 3 in conversation 1). These are attached to a date, and reset for each day. The log directory structure reflects the groupings.

There are two ways classification is performed: by idle timeout, or via LLM. The `ENABLE_DIAGNOSTIC_AGENT` flag determines which method is used for classification. If false (the default), a new conversation starts when the gap between requests exceeds `CONVERSATION_IDLE_TIMEOUT_SECONDS` (default 300s) or on WebSocket reconnection. If set to true, an LLM call classifies the user input into a conversation thread; it sees the list of active conversations and decides: continue an existing thread (reuse its `cXX` ID) or start a new one (mint `cXX+1`). A lightweight model (e.g. Haiku class) is generally sufficient for this purpose.

### 2. System prompt assembly

`_build_system_message()` constructs a single system prompt from two parts:

**Static base prompt** (`coordinator_system_prompt` on RuntimeState): defines the agent's role, output conventions, and general behaviour rules. This is set once at startup and doesn't change between requests.

**Dynamic context sections**: assembled from registered `ContextProvider` instances. Each provider contributes a titled section:

| Provider | Content |
|---|---|
| `CurrentDateProvider` | Current date |
| `CurrentTimeProvider` | Current time |
| `ConversationHistoryProvider` | Recent user/agent turns (rolling window) |
| `TextContextProvider` (execution trace) | Summary of recent tool calls and their outcomes |
| `TextContextProvider` (search history) | Cached result handles with descriptions |
| `CallbackContextProvider` (skills) | All SKILL.md content, loaded statically at startup |
| `CallbackContextProvider` (zone aliases) | User-defined zone name mappings |
| `CallbackContextProvider` (zone status) | Current playback status and available zones |
| `TextContextProvider` (key rules) | Key behavioural rules, positioned for recency bias |

The complete prompt is assembled once per request. It is not rebuilt between tool loop steps — within a request, context accumulates naturally through the LLM conversation (assistant tool-call messages followed by tool result messages).

**Analyser contract:** the assembled prompt is written verbatim to `prompts/coordinator_system.txt`; the passive analyser embeds it as-is. Adding, removing, renaming, or reordering a provider flows through to the analyser payload automatically — but `agent/analyser/analysis-guide.md` enumerates the providers by name in its "Payload structure" section, so update that doc alongside the code change.

### 3. Tool-calling loop

The loop in `tool_loop.py` is the heart of the system. It runs a straightforward cycle:

```
for each step up to hard_limit (default 12):
    1. Send messages + tool schemas to LLM
    2. If LLM returns text → done, return it
    3. If LLM returns tool calls:
       a. Append assistant message (with tool calls) to conversation
       b. Execute each tool via ToolRegistry (parallel if PARALLEL_TOOLS enabled)
       c. Optionally compact the tool output
       d. Append tool result messages to conversation
       e. Continue to next step
```

**Parallel tool execution**: when `PARALLEL_TOOLS` is enabled, tools marked as `parallel_safe` execute concurrently within a step via `asyncio.gather` + `asyncio.to_thread`. `ROON_MAX_PARALLEL` limits the number of concurrent Roon operations per step by batching — batches run sequentially, calls within a batch run concurrently. The default cap is 5 (keeps the Roon Core from dropping or stalling responses on large multi-track requests); set to 0 (or any value `< 1`) to disable batching entirely. Step duration represents wall-clock time (max of concurrent calls) rather than sum of sequential durations.

**Step limits**: the hard limit (default 12) terminates the loop unconditionally. A soft nudge is injected at step 8 as a system message encouraging the model to wrap up if it's stuck.

**Loop detection**: if the last two tool calls are identical (same name and arguments), the loop injects a system message telling the model it's repeating itself.

**Tool output compaction**: search results are compacted into a one-line-per-item format before going back into the conversation, keeping the context window manageable. Other tools return their default JSON serialisation.

**Result handles**: after tool execution, a post-processing hook checks whether a new result handle was created (for paginated results) and annotates the tool output with `[Result handle: ...]` so the LLM knows how to reference those results later.

### 4. Tool execution

Tools are plain Pydantic models. Each tool has:

- An **input schema** (Pydantic `BaseModel`) that defines the parameters the LLM can pass
- An **async execute method** that receives the validated input and returns an output model
- A **description** used in the tool schema sent to the LLM

The `ToolRegistry` handles dispatching: it deserialises the LLM's JSON arguments into the Pydantic input model, calls the executor, and returns the output. Registration happens in `RuntimeState.initialise()`.

Current tools:

| Tool | Purpose |
|---|---|
| `roon_search` | Browse and search the Roon library |
| `roon_action` | Playback control: play, queue, pause, skip, seek, shuffle, radio |
| `roon_status` | Get zone status, now-playing info, and queue contents |
| `roon_config` | Set default zone, manage zone aliases, transfer playback |
| `result_fetch` | Retrieve cached search results by handle |
| `web_search` | Web search for general knowledge questions (configurable backend: Brave, Tavily, or self-hosted SearXNG) |

### 5. Response extraction

After the loop completes:

1. The raw text is processed through tag expansion — `<queue zone="..."/>` tags are replaced with formatted queue listings, `<list>` tags are expanded from result handles
2. The text is sanitised to remove any leaked structured fields (a safety net for when the LLM accidentally outputs JSON fragments)
3. A TTS-friendly version is derived (more concise, and stripped of markdown, emojis, and non-speakable content)
4. The response is emitted on the `chat` WebSocket channel with the request ID and TTS text in metadata
5. The conversation history provider is updated for cross-request context

## Cross-request context

Between requests, three pieces of context carry forward via the system prompt:

**Execution trace**: a rolling summary of recent tool calls and their outcomes. Each entry records what tool was called, what it was asked to do, and a compact version of what it returned. This gives the LLM continuity across requests — it knows what it already searched for and what it found.

**Search history**: cached result handles with human-readable descriptions. When the LLM searches for something, the results are stored in memory and a handle is created (e.g. `res_00001`). The search history section lists all active handles so the LLM can reference previous results without re-searching.

**Conversation history**: recent user/agent turns in a rolling window (default 5 turns). This provides conversational continuity — the LLM can see what the user asked before and what it responded with.

All three are injected into the system prompt at the start of each request. They are not rebuilt mid-request.

## Interrupt handling

When a new WebSocket message arrives while a request is in-flight, the system must decide what to do. This is handled by `websocket_flow.py`.

**Explicit interrupts**: messages containing stop/cancel keywords bypass the LLM and immediately cancel the in-flight request via a `threading.Event`.

**Ambiguous interrupts**: for other messages, an optional **interrupt arbiter** (enabled via `ENABLE_INTERRUPT_ARBITER`, off by default — otherwise new messages simply queue) classifies the situation, using a lightweight LLM call with the arbiter model, into one of three actions:

| Action | Meaning |
|---|---|
| `queue` | New message is a follow-up; queue it behind the current request |
| `interrupt_and_replace` | New message is unrelated; cancel current and process new |
| `interrupt_only` | New message is an explicit stop command |

The arbiter sees a summary of the active request and the new message, and makes a judgement call. If it fails or times out, the default is `queue`.

## Conversation tracking and logging

### Request IDs

Every request gets a sequential ID in the format `rq-cNN-NNNN`:

- `cNN` is the conversation identifier (e.g. `c01`, `c02`)
- `NNNN` is a monotonic request counter within each conversation

The `RequestIdGenerator` (one per session) manages this state and resumes counters from existing log directories on restart.

### Log structure

Two log systems run in parallel:

**Conversation logs** at `data/logs/conversation/<date>/<cNN>/<request-id>/`:

| File | Content |
|---|---|
| `request.json` | User input, request ID, timestamp |
| `coordinator_steps/step_NN.yaml` | Full LLM input/output per step |
| `tool_executions/NN_<skill>.json` | Tool I/O, timing |
| `prompts/` | System prompt and context provider content |
| `outcome.json` | Final status, chat response, timing, topic summary |
| `events.jsonl` | Chronological stream of all WS emissions |

**Server logs** at `data/logs/server/<date>/<cNN>/<request-id>/server.yaml`:

Detailed YAML traces of Roon API operations (browse calls, action execution, zone lookups). Each entry is a separate YAML document. Useful for debugging why a search or action didn't find the right item.

Both log types are retained for 7 days by default (`LOG_RETENTION_DAYS`), cleaned up on startup.

## Roon connection

`RoonConnection` in `roon_core/connection.py` is assembled from several mixins:

| Mixin | Responsibility |
|---|---|
| `RoonAuthMixin` | Token persistence, pairing handshake with Roon Core |
| `RoonBrowseMixin` | Library browse/search with multi-session support |
| `RoonZoneMixin` | Zone enumeration, display names, default zone |
| `RoonPlaybackMixin` | Transport controls, queue actions, seek, transfer |
| `RoonEventsMixin` | Subscription to Roon state-change callbacks |

**Parallel browse dispatch**: `roon_core/parallel_browse.py` patches the Roon socket layer to support concurrent browse operations. It replaces the default sequential request-response flow with a Future-based correlation system — each outgoing request is tagged with a `request_id`, and the patched `on_message` handler resolves the matching Future when the response arrives. This allows multiple browse/search calls to be in-flight simultaneously without cross-thread interference. The patch is applied dynamically and re-applied after reconnection.

The connection is established at startup (auto-discovered or via explicit `ROON_CORE_URL`). On first launch, Swarpius appears in Roon Settings > Extensions and must be authorised. The auth token is persisted in the data directory for subsequent launches.

### Zone management

Zones are Roon's concept of an audio output (a speaker, a group of speakers, etc.). Swarpius adds:

- **Zone aliases**: user-defined short names (e.g. "lounge" → "Chord Qutest") stored in `data/config/zone_aliases.json`
- **Fuzzy zone resolution**: when the user or LLM references a zone by a partial or approximate name, `_resolve_zone_name_fuzzy()` tries exact match, alias match, substring match, and token overlap scoring
- **Queue references**: lifecycle-persisted reference IDs for queue items, minted when items appear in Roon subscription events and invalidated when removed. Stored in `QueueReferenceMap` per zone.

### Browse sessions

Roon's browse API is stateful and session-based. Each search creates a new browse session, and navigation (drill-down, pop) changes the cursor position within that session. See `docs/how-roon-browse-works.md` for a detailed reference on the API's behaviour and our stable reference system.

## State persistence

State persists across a restart so the user can continue as though the agent had never stopped. The store is a single SQLite database (`messages.db`) owned by `StateDb` (one connection + lock); the schema is versioned with `PRAGMA user_version` and upgraded through chained N→N+1 migrations (`app/io/db_schema.py`). A corrupt or future-versioned DB is backed up and recreated rather than crashing startup.

**What persists**

- **Chat transcript** — every WS message on the persisted channels, used both for replay on connect and for history browsing.
- **Working memory** — recent conversation turns, the execution trace, and cached search results / handles, so the model and `<list>`/`<queue>` tags keep resolving.
- **Roon references** — the browse-session reference pool and queue references, so item keys and handles still resolve after reconnecting to the Core (the `_semantic_recover` rewalk remains the fallback if a key has gone stale).
- **Default zone** — set once and remembered (replacing the former `DEFAULT_ROON_ZONE` env var).
- **Conversation tracker** — the process-level `RequestIdGenerator`/`ConversationTracker` (thread state + counters, wall-clock timestamps), so conversation grouping and request-ID numbering continue across a restart.
- **Listening history** — a record of recently played tracks per zone, queryable by the `listening_history` tool.

**Load / commit model**

- **Read once at startup.** `PersistenceManager` reads the whole saved state into a bag; each participant (a `PersistentState` with `capture_state`/`restore_state`) applies its slice as it is constructed. Restoring everything up front keeps it simple — nothing depends on the WS or Roon connection being established first.
- **Commit at the request terminal.** After each completed request, the registered participants are captured and written in one transaction — gated on a restart not being in progress, so a request dropped by a restart leaves no half-written state. Working memory drops any restored turn older than the chat-retention cutoff (it can never exceed what the transcript retains).

**Retention.** A startup sweep (`app/io/history_retention.py`) prunes by age with independent windows: `CHAT_HISTORY_RETENTION_DAYS` (chat, default 90), `DIAGNOSTICS_RETENTION_DAYS` (agent/tool/LLM events, default 30), and `LISTENING_HISTORY_RETENTION_DAYS` (default 365). `0` keeps that data indefinitely.

**History browsing (web client).** Only the most recent non-empty day loads on connect; the chat lazy-loads earlier days on scroll-up (skipping empty days) and via a date picker, using one server primitive — "the messages for the day at or before timestamp T". Requests are fire-and-forget; the client's passive receive sorts and de-dupes whatever arrives by a stable server message id, so live messages, replay, and lazy-loaded history all assemble through one path. See `docs/web-client.md`.

## Model profiles

Per-model tuning is configured in `model_profiles.yaml` and managed by `model_profiles.py`. Profiles are matched by regex against the full `provider/model` string (first match wins); models without a matching profile get the defaults. Prompts, context, and validation are the same for all models — only the loop limits and generation parameters vary.

See [`model-profiles.md`](./model-profiles.md) for the full field reference (temperature, top_p, coordinator step caps, generation params, `temperature_lock` etc.) and examples.

## WebSocket channels

The agent communicates with the web client over a single WebSocket connection. Messages are JSON objects with a `channel` field that determines the message type and a `payload` field with the data.

See the [`agent/README.md`](../agent/README.md) for the full channel reference table.
