# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Before any code or test edits**, read [`docs/coding-standards.md`](docs/coding-standards.md). The criteria there govern code review on this repo and supersede general defaults (comment discipline, fake-tautology test prevention, criteria-precedence rules, etc.).

## Repository Overview

Swarpius is an LLM-driven chat assistant for controlling a Roon music player. It is a monorepo with these services:

- `agent/`: Python 3.13 backend: LLM orchestration, Roon tools, WebSocket API. Includes `agent/analyser/` — the in-process LLM-based conversation-quality analyser (failure-mode classification against a 19-mode taxonomy). Off by default; toggled via `ENABLE_PASSIVE_ANALYSER=true`.
- `web-client/`: React/Vite frontend: chat UI, playback status, diagnostics panels
- `tts-server/` and `searxng/`: local config for optional compose services (opt-in via `--profile tts` and `--profile search`)

## Commands

### Agent (Python) — run from `agent/`

```bash
# Install (Linux/macOS/WSL)
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt        # default — includes PyAudio + numpy for CLI-mode TTS (needs portaudio system lib)
python3 -m pip install -r requirements-server.txt  # WS / Docker / headless — no audio libs, no portaudio dep
python3 -m pip install -r requirements-dev.txt     # adds pytest, ruff (transitively includes audio deps)

# WSL: use the ./dev wrapper (activates .venv-wsl, fixes WSL tmp issues)

# Run (CLI mode — default)
# Startup banner + Rich spinners + readline history at
# $SWARPIUS_DATA_DIR/cli_history. Slash commands: /exit, /usage.
# Add --show-request-ids to surface request IDs on the User/Swarpius panels.
python3 swarpius.py

# Run (WebSocket mode for the web client / Docker)
# Bind via SWARPIUS_WS_HOST / SWARPIUS_WS_PORT env vars. The agent
# defaults to 127.0.0.1:8080 (loopback); set SWARPIUS_WS_HOST=0.0.0.0 to
# expose. Docker sets 0.0.0.0 in the container and gates host exposure
# via SWARPIUS_BIND_IP — see SECURITY.md "Network exposure".
python3 swarpius.py --ws

# Lint and test (after `source .venv/bin/activate`)

# Lint
ruff check .

# Test — offline tests (default, excludes live Roon tests)
pytest

# Test — live Roon tests (requires running Roon Core; reads config from .env)
pytest -m live_roon

# Test — all tests
pytest -m ""

# Test — single module
pytest tests/test_browse_session.py

# Test — single test
pytest tests/test_browse_session.py::TestBrowseSession::test_some_method

# WSL convenience: `./dev <command>` activates the WSL-specific venv
# (`.venv-wsl`) and works around a pytest tmpdir issue on WSL.
# E.g. `./dev pytest`, `./dev ruff check .`. Native Linux/macOS users
# don't need it.
```

### Web Client — run from `web-client/`

```bash
npm install
npm run dev        # dev server at http://localhost:5173
npm run build      # type-check + production build
npm run lint       # ESLint
npm test           # Vitest
```

### Docker Compose — run from repo root

```bash
docker compose up -d        # core services (agent + web client)
docker compose up -d swarpius-agent   # agent only
docker compose down
```

Optional services run via `--profile <name>` (or `--profile all` to enable
everything):

| Profile | Service | Purpose |
|---|---|---|
| `search` | `searxng` | Self-hosted web search backend |
| `tts` | `tts-server` | F5-TTS server for spoken output |
| `all` | (all of the above) | Full stack |

Example: `docker compose --profile search --profile tts up -d`.

The conversation-quality analyser used to be a separate service
(`--profile analysis`). It now runs in-process inside the agent
container — set `ENABLE_PASSIVE_ANALYSER=true` in `agent/.env` to
turn it on. The "Scan & Analyse" and "Re-Analyse" buttons in the
web UI work even with the background loop off.

## Agent Architecture

### Request flow

The agent uses a native tool-calling loop with no framework dependencies. Tools are plain Pydantic models with async execute methods. LiteLLM is the only LLM dependency, providing multi-provider support.

Every user request goes through this pipeline in `app/coordinator/request_flow.py`:

1. **Conversation classification** — if `ENABLE_DIAGNOSTIC_AGENT` is true, a lightweight `DiagnosticAgent` classifies the user input into a conversation thread before anything else runs. It sees the list of active conversations and decides: continue an existing thread (reuse its `cXX` ID) or start a new one (mint `cXX+1`). The result is applied to the `ConversationTracker` so that the request ID and log directory reflect the semantic classification. Falls back to timeout-based assignment if the agent is disabled or fails.

2. **System prompt assembly** — `_build_system_message()` builds a single system prompt from the static base prompt (`coordinator_system_prompt`) plus dynamic context sections from all registered providers (current date/time, conversation history, execution trace, search history, skill definitions). This prompt is assembled once per request and is not rebuilt between tool loop steps.

3. **Native tool-calling loop** — `run_tool_loop()` in `tool_loop.py` sends the message list (system + user) along with the full tool registry to the LLM via LiteLLM. The LLM natively decides whether to call a tool or produce a text response. There is no separate intent router — the LLM selects tools directly from the full registry. Within a request, context accumulates naturally through the LLM conversation: assistant tool-call messages followed by tool result messages build up the context window.

4. **Tool execution** — when the LLM returns tool calls, `ToolRegistry.execute()` dispatches each one by name, deserialising arguments into the Pydantic input model and calling the async executor. Tool outputs are optionally compacted (e.g. roon_search results become one-line-per-item format) before being placed back into the conversation.

5. **Loop termination** — the loop terminates when the LLM produces a text response (no tool calls), when the hard step limit is hit (default 12), or on error. A soft nudge is injected at step 8 to encourage wrapping up. Loop detection catches identical consecutive tool calls.

6. **Response extraction** — the final text output is sanitised via `sanitise_agent_chat_text()` and emitted as the chat response. The conversation history provider is updated for cross-request context.

Cross-request context is maintained through providers in the system prompt: execution trace (recent tool calls and their outcomes), search history (cached result handles), and conversation history (recent user/agent turns).

### Key modules

| Module | Purpose |
|---|---|
| `swarpius.py` | Supervisor; spawns `agent.py` as a child and respawns it on exit code 75 (Restart). Bypassed in Docker — compose's restart policy plays the supervisor role |
| `agent.py` | The agent itself; CLI vs WS mode selection, request loop, WS server. Invoked directly by Docker / via the supervisor for everything else |
| `app/coordinator/request_flow.py` | Prompt assembly, tool loop invocation, response extraction, observability callbacks, logging |
| `app/llm/tool_loop.py` | Core tool-calling loop: LLM call, tool execution, repeat until text or step limit |
| `app/llm/tool_registry.py` | Tool registration, JSON Schema generation for LLM API, dispatch by name |
| `app/llm/client.py` | Thin LiteLLM wrapper; `LLMClient`, `LLMResponse`, `ToolCall` dataclasses |
| `app/runtime/state.py` | Central state: providers, search history, execution trace, result store, tool registration |
| `app/coordinator/context_providers.py` | Dynamic context providers (date, time, conversation history, text-based) |
| `app/io/core.py` | `AppIO`: WebSocket broadcast, chat emission, TTS routing |
| `app/io/websocket_flow.py` | Per-connection session state, interrupt arbitration, task queue |
| `app/runtime/conversation_tracker.py` | Conversation thread management: timeout and diagnostic-agent-driven assignment. Wall-clock timestamps + capture/restore so grouping survives a restart |
| `app/llm/diagnostic_agent.py` | Lightweight LLM agent for semantic conversation classification |
| `app/runtime/request_logger.py` | Per-request structured logging, `RequestIdGenerator` (process-level on `RuntimeState`, shared by WS + CLI; a persistence participant), conversation grouping |
| `app/runtime/server_logger.py` | Server-side YAML logger for detailed browse/action traces |
| `app/coordinator/skill_loader.py` | Per-tool SKILL.md loader; frontmatter validation; critical-directive extraction |
| `app/coordinator/trace.py` | Execution-trace serialisation + context compaction |
| `app/coordinator/sanitise.py` | Chat / TTS text cleanup (leak detection, markdown stripping, emoji removal) |
| `app/llm/rate_limit.py` | Provider rate-limit detection + banner emission |
| `app/runtime/cancellation.py` | Cooperative request cancellation helpers |
| `app/runtime/url_parse.py` | Host:port parsing for Roon Core URLs |
| `app/io/redact.py` | Secret redaction for logs / WS error payloads |
| `app/io/state_db.py` | `StateDb`: owns the single SQLite connection + lock behind `messages.db`; `transaction()` context manager |
| `app/io/db_schema.py` | `messages.db` schema, `PRAGMA user_version` versioning, chained N→N+1 migrations, corrupt/future-DB backup-and-reset |
| `app/io/message_store.py` | `SqliteMessageStore`: persisted WS messages for replay + the `load_day` / `load_range` history-browsing queries |
| `app/io/history_retention.py` | Startup prune of `ws_messages` (chat vs diagnostics windows) + listening history by age |
| `app/runtime/persistence.py` | `PersistenceManager` + `PersistentState` protocol: read-all-once-at-startup, register participants, commit in one transaction |
| `app/runtime/working_memory_persistence.py` | `WorkingMemoryState` participant: capture/restore conversation turns, execution trace, search results (drops turns older than chat retention) |
| `app/runtime/roon_persistence.py` | `QueueRefsState` + `DefaultZoneState` participants for Roon reference + default-zone persistence |
| `app/roon/listening_history.py` | `ListeningHistoryStore`: per-zone track detection from zone events, recorded to `messages.db`; queried by date/zone |
| `app/roon/tag_expansion.py` | `<list ref="…"/>` and `<queue zone="…"/>` chat-tag expansion |
| `app/roon/zone_formatting.py` | Compact zone / playback status formatting for LLM context |
| `app/schemas.py` | `InterruptArbiterOutputSchema` |
| `app/llm/model_profiles.py` | Per-model runtime knobs (step limits), profile-driven tuning, YAML config loading |
| `model_profiles.yaml` | Per-model/provider tuning: temperature, top_p, coordinator loop limits, generation params. See `docs/model-profiles.md`. |
| `app/constants.py` | Channel names, step limits, regex patterns, thresholds |
| `app/exceptions.py` | Custom exceptions (`RequestInterrupted`, `ZoneLookupError`, etc.) |
| `app/coordinator/skill_docs.py` | `AgentSkillDocument` / `AgentSkillMetadata` dataclasses for parsed SKILL.md files |
| `skills/*/SKILL.md` | Per-tool prompt guidance loaded at startup; frontmatter controls inclusion |
| `tools/` | Tool implementations with Pydantic input/output schemas and async execute methods |
| `usage_metrics.py` | Token accounting per call and session-level rollups |
| `app/io/cost_ledger.py` | `CostLedger`: one row per LLM agent invocation in `messages.db` (agent/model/tokens/cost/steps), `aggregate()` for the cost dashboard; module-level `get/set_cost_ledger` + `record_cost`/`record_cost_from_usage` helpers used by every LLM consumer |
| `app/cli/history.py` | Readline history persistence for CLI mode (load on entry, save in finally; safe when readline is missing) |
| `app/cli/runner.py` | Two-tap Ctrl+C cancellation wrapper for CLI requests (`CancelHandler` + daemon-thread runner) |
| `app/cli/telemetry.py` | Per-request usage one-liner formatter |
| `app/cli/session_usage.py` | Session-level usage aggregator + `/usage` detailed view; `format_cost_overview()` renders the all-time cost block (from the cost ledger) for `/usage` |
| `app/cli/log_routing.py` | Routes INFO chatter to file (CLI mode + installer bundle); bumps stderr handler to WARNING |
| `app/cli/startup_banner.py` | Renders the at-a-glance startup banner (Roon Core, models, web search, TTS, etc.) |

### Roon API modules (`roon_core/`)

`RoonConnection` in `roon_core/connection.py` composes several mixins.

| Module | Purpose |
|---|---|
| `roon_core/connection.py` | `RoonConnection` class: assembles all mixins, manages discovery and the API transport |
| `roon_core/auth.py` | `RoonAuthMixin`: token persistence, pairing handshake |
| `roon_core/browse.py` | `RoonBrowseMixin`: library browse/search with multi-session support |
| `roon_core/browse_session.py` | `BrowseSession` / `BrowseSessionManager`: per-session browse state and hierarchy tracking |
| `roon_core/zones.py` | `RoonZoneMixin`: zone enumeration, display names, default zone management |
| `roon_core/playback.py` | `RoonPlaybackMixin`: transport controls, queue actions, seek, transfer |
| `roon_core/events.py` | `RoonEventsMixin`: subscription to Roon state-change callbacks |
| `roon_core/parallel_browse.py` | Future-based parallel browse dispatch: patches Roon socket for concurrent request-response correlation |
| `roon_core/queue_references.py` | Lifecycle-persisted queue reference map, minted from Roon subscription events |
| `roon_core/schemas.py` | Pydantic models for Roon API data (zone status, now-playing, browse items) |

### Tools / skills

| Skill name | Tool class | Description |
|---|---|---|
| `roon_search` | `RoonSearchTool` | Browse/search Roon library |
| `roon_action` | `RoonActionTool` | Transport controls and queue actions |
| `roon_status` | `RoonStatusTool` | Zone status and now-playing info |
| `roon_config` | `RoonConfigTool` | Zone alias and config management |
| `web_search` | provider-neutral base + `BraveSearchTool` / `TavilySearchTool` / `SearXNGSearchTool` | Web search via configurable backend (Brave, Tavily, or self-hosted SearXNG) |
| `result_fetch` | `ResultFetchTool` | Paginate/retrieve cached search results |
| `listening_history` | `ListeningHistoryTool` | Query recently played tracks ("what did I listen to") with optional date-range / zone filters |

### WebSocket channels

Messages are JSON `{"channel": "<name>", "payload": ...}`. Key channels:

- `chat`: user messages in / agent `chat_response` out
- `agent-outputs`, `tool-outputs`: diagnostic event streams
- `usage-metrics`, `llm-diagnostics`, `rate-limit`: telemetry
- `cost-metrics-request/response`: cost dashboard query (range + agent/model filter) → ledger aggregate
- `zone-snapshots`: holistic snapshot of every Roon zone, re-emitted on any change
- `session-control-request/response`: interrupt/cancel controls
- `roon-control-request/response`: direct Roon control from frontend
- `roon-image-request/response`: image fetching for zone artwork
- `errors`: error reporting

### Request logging

Every request is assigned a sequential ID in the format `rq-cNN-NNNN` (e.g. `rq-c01-0003`), where `cNN` is a conversation identifier and `NNNN` is a monotonic request sequence. When the diagnostic agent is enabled, `cNN` reflects semantic conversation classification — requests about the same topic share a `cNN` even across idle gaps. When disabled, a new conversation starts when the idle gap exceeds `CONVERSATION_IDLE_TIMEOUT_SECONDS` (default 5 minutes) or on WebSocket reconnection. `RequestIdGenerator` (one per session) manages this state and resumes counters from existing log directories on restart.

Two log systems operate in parallel:

- **Conversation logs**: `agent/data/logs/conversation/<date>/<cNN>/<request-id>/`
  - `conversation_summary.json`: (at `cNN/` level) topic summary and request list for the conversation
  - `request.json`: user input, request ID, timestamp
  - `coordinator_steps/step_NN.yaml`: LLM input/output per step
  - `tool_executions/NN_<skill>.json`: full tool I/O, timing
  - `prompts/`: system prompts and context provider content
  - `outcome.json`: final status, chat response, total timing; includes `topic_summary` and `assignment_source` when diagnostic agent is active
  - `events.jsonl`: chronological stream of all WS emissions for the request

- **Server logs**: `agent/data/logs/server/<date>/<cNN>/<request-id>/server.yaml`
  - Detailed YAML trace of browse/action operations within Roon API calls
  - Each entry is a YAML document separated by `---`

Request IDs are displayed in the frontend chat panel and LLM diagnostics panel (click to copy). Both log types are retained for 7 days by default (configurable via `LOG_RETENTION_DAYS` env var), cleaned up on startup.

### Interrupt handling

The interrupt arbiter is opt-in (`ENABLE_INTERRUPT_ARBITER`, default off — otherwise a new message simply queues behind the in-flight one). When enabled, a new WS message arriving mid-request triggers `arbitrate_interrupt`, a lightweight LLM call (via the arbiter client) that decides: `queue`, `interrupt_and_replace`, or `interrupt_only`. Explicit stop/cancel commands bypass the LLM call. Cancellation is propagated via `threading.Event` passed through the executor.

## Web Client Architecture

React 19 app using Vite (rolldown-vite). `WebSocketProvider` (`WebSocketProvider.tsx`) manages the connection and exposes messages via `useWebSocket()` hook (`websocketContext.ts`). Config (`config.ts`) handles WS URL derivation — loopback addresses are rewritten to the browser host for LAN access.

### Components

| Component | Purpose |
|---|---|
| `ChatPanel` | Message list, draft composer, rate-limit banners, TTS auto-play |
| `ZoneStatusPanel` | Live playback and artwork driven by the `zone-snapshots` channel |
| `LlmDiagnosticsPanel` | Active/completed LLM calls, prompt token breakdowns, interrupt decisions, request ID |
| `PromptBudgetPanel` | Rolling 60-second and session-wide token aggregation by provider |
| `RequestSummaryPanel` | Per-request summary cards with timing and step counts |
| `SessionSummaryBar` | Session-level usage summary bar |
| `CostDashboard` | Always-available cost view (header `$` icon, not Developer Mode): LLM spend over time, by agent, by model, and mean cost per request by complexity, with date-range + agent/model filters. Requests `cost-metrics` and renders the ledger aggregate; reuses the analysis metrics chart styling |
| `HistoryWindow` | Scrollable diagnostic-channel history (per channel); lazy-loads older days on scroll-up and syncs to a focused request |
| `FormattedMessageBody` | Renders message bodies: source labels, JSON prettification, plan extraction |
| `RequestIdBadge` | Request ID badge: copies to clipboard; when on a request-aware surface (`syncKey`), the id focuses that request across all open panels |
| `HistoryDatePicker` | Calendar icon in the chat header; opens the native date picker to jump to a day |
| `Settings/PrivacyTab` | "Privacy & Data" settings tab: clear conversation history / clear listening history |
| `TtsToggle` | Auto-TTS on/off toggle |

### Supporting modules

| Module | Purpose |
|---|---|
| `tts.tsx` | TTS WebSocket client for streaming audio from the F5 TTS server |
| `utils/formatMessageBody.ts` | Parses raw WS message bodies: extracts source labels, sanitises chat leaks, combines chat + details |
| `hooks/useDiagnostics.ts` | Shared hook for diagnostics state across panels |
| `hooks/useHistoryScrollback.ts` | Lazy-loads older days on scroll-up (skip-empty), anchors the viewport on prepend; auto-fill optional |
| `hooks/useRequestFocusSync.ts` | Scrolls a panel to (and flashes) the focused request; `scrollRequestIntoView` helper shared with the date jump |
| `hooks/useScrollToBottomButton.ts` | Tracks distance-from-bottom; drives the transient scroll-to-bottom button (show / highlight-on-new / `scrollToBottom`) |
| `ScrollableViewport.tsx` / `ScrollToBottomButton.tsx` | Shared wrapper + button: a fade-in jump-to-bottom affordance over the chat and Agents/Tools/Errors scroll areas |
| `RequestFocusProvider.tsx` / `requestFocusContext.ts` | Holds the focused request (id + source); `useRequestFocus()` for badges/panels |
| `utils/insertMessage.ts` | Passive sorted-insert + dedup of incoming messages by server id (live, replay, lazy-load all flow through it) |
| `utils/dayLabel.ts` | Day-separator label ("Today"/"Yesterday"/date) + same-day comparison |
| `components/zoneStatusModel.ts` / `zoneStatusUtils.ts` | Zone state model and artwork/seek helpers for `ZoneStatusPanel` |

TTS playback opens a WS connection to the agent's `/tts` path on the same port as chat — the agent proxies bytes to F5-TTS over TCP. The URL is derived from the chat WS URL at runtime; nothing build-time to configure.

## Environment Setup

Agent env variables live in `agent/.env` (copy from `agent/.env.template`). Required:

- `LLM_MODEL`: default model in `provider/model` format (e.g. `anthropic/claude-sonnet-4-6`)
- `LLM_API_KEY_<PROVIDER>`: API key per provider (e.g. `LLM_API_KEY_ANTHROPIC`, `LLM_API_KEY_OPENAI`). Local providers like Ollama need no key.

Web search backend (optional but strongly recommended — coordinator can't answer external-knowledge queries without one). Pick **one**:

- `BRAVE_API_KEY`: [Brave Search](https://brave.com/search/api/), free tier covers casual use
- `TAVILY_API_KEY`: [Tavily](https://tavily.com/), managed alternative
- `SEARXNG_URL`: self-hosted [SearXNG](https://github.com/searxng/searxng), bundled in the Docker compose stack under `--profile search`
- `WEB_SEARCH_PROVIDER`: required to enable web search. Values: `brave`, `tavily`, `searxng`, or `none` (or unset → disabled). Each provider also requires its own credential — `BRAVE_API_KEY`, `TAVILY_API_KEY`, or `SEARXNG_URL` respectively. For Docker compose users with `--profile search`, `SEARXNG_URL` is injected automatically by compose; users running from source must set it explicitly.

Roon (all optional — auto-discovery fills the gaps): the default zone is chosen
automatically (first zone the Core reports) and persisted once changed via the
web client — there is no `DEFAULT_ROON_ZONE` env var.

Agent optional — per-agent model overrides for the lightweight sub-agents (default to `LLM_MODEL`). The coordinator always uses `LLM_MODEL` directly. Include provider prefix if different from the default:

- `LLM_MODEL_ARBITER`: model for the Interrupt Arbiter
- `LLM_MODEL_DIAGNOSTIC`: model for the Diagnostic Agent

LLM tuning (temperature, top_p, coordinator loop limits, provider-specific flags like `think=False`) is configured in `agent/model_profiles.yaml`, not env vars. See `docs/model-profiles.md`.

Agent optional — feature flags and tuning:

- `ENABLE_DIAGNOSTIC_AGENT`: enable LLM-driven conversation classification (`true`/`false`, default `false`)
- `ENABLE_INTERRUPT_ARBITER`: enable the LLM interrupt arbiter (classifies a new message during an in-flight request as queue / interrupt-and-replace / interrupt-only). Off by default — new messages queue. (`true`/`false`, default `false`)
- `ENABLE_PROMPT_CACHING`: add `cache_control` markers to system messages and tool definitions. Applied when the coordinator model prefix is `anthropic/`, `gemini/`, or `vertex_ai/` (see `_CACHE_CONTROL_MODEL_PREFIXES` in `request_flow.py`); OpenAI/DeepSeek cache automatically without markers, so the flag has no effect there. (`true`/`false`, default `true`)
- `PARALLEL_TOOLS`: run parallelisable tool calls concurrently within a step via `asyncio.gather` + `asyncio.to_thread`. (`true`/`false`, default `false`)
- `ROON_MAX_PARALLEL`: maximum number of parallel Roon operations per step. Parallel-safe tool calls are batched into groups of this size — batches run sequentially, calls within a batch run concurrently. (integer, default `5`. Set to a positive integer to override; set to `0` or any value `< 1` for unlimited. The default keeps Roon Cores from dropping or stalling responses on large multi-track requests.)
- `ROON_SEARCH_RETRY_LIMIT`: maximum retries when Roon search returns a transient empty result. (`integer`, default `2`)
- `ROON_SEARCH_RETRY_DELAY`: seconds to wait before retrying a failed search. (`float`, default `1.0`)

Agent optional — storage, connection, logging, TTS:

- `SWARPIUS_DATA_DIR`: base directory for persistent state — CLI history, conversation/server logs, message DB. Defaults to `agent/data/`.
- `ROON_CORE_URL`: explicit Roon Core address (auto-discovered if omitted)
- `ROON_CORE_NAME`: when multiple Cores are on the network, the friendly name of the Core to pair with (matches Roon's Settings > General > Name, case-insensitive). Ignored when only one Core is discovered
- `ROON_PROFILE_NAME`: Roon profile to authenticate as
- `LOG_FILE`: path to a server log file (e.g. `logs/swarpius.log`). Rotates at 10MB, keeps 3 backups. **Default**: when unset, every mode (CLI, WS source, WS bundle, Docker) writes to `<SWARPIUS_DATA_DIR>/logs/swarpius.log`. Set this to override the path. CLI / bundle modes additionally silence stderr; WS source / Docker keep stderr alongside the file.
- `LOG_RETENTION_DAYS`: request/server **log** retention in days (default `7`) — distinct from the persisted-history retention below, which governs `messages.db`
- `CHAT_HISTORY_RETENTION_DAYS`: persisted chat transcript + working memory retention in days (default `90`; `0` = keep forever)
- `DIAGNOSTICS_RETENTION_DAYS`: persisted diagnostics (agent/tool/LLM events) retention in days (default `30`; `0` = keep forever)
- `LISTENING_HISTORY_RETENTION_DAYS`: listening-history retention in days (default `365`; `0` = keep forever)
- `CONVERSATION_IDLE_TIMEOUT_SECONDS`: idle gap that starts a new conversation group in logs (default `300`)
- `TTS_URL`: F5-TTS server address as scheme-less `host:port` (TCP). The agent uses it directly for CLI-mode speech AND proxies it to the browser over its own WebSocket (`/tts` path on the agent's main port), so one setting drives both modes. Leave unset to disable.

Web client env variables live in `web-client/.env` (copy from `web-client/.env.template`). Optional:

- `VITE_WS_URL`

## Working Conventions

- **Always run linter alongside tests.** When running `pytest`, also run `ruff check .` and fix any lint errors before considering the task done.
- **Line endings:** repo is LF-only. On Windows, develop inside WSL on the WSL filesystem (not a Windows-mounted drive); set `core.autocrlf=input` globally so git commits LF and never converts on checkout.

## Further reading

For deeper context on a specific area, follow up with:

- **Top-level docs** (`docs/`): `architecture.md`, `tool-system.md`, `model-profiles.md`, `tts-adapters.md`, `web-client.md`, `how-roon-browse-works.md`, `category-reconciliation.md`, `logging.md`, `loop-detection.md`, `known-limitations.md`.
- **Per-component READMEs**: `agent/README.md`, `web-client/README.md`, `tts-server/README.md`, `searxng/README.md`, `agent/data/README.md`. For the analyser see `docs/analyser.md`.
- **Skills**: per-tool prompt guidance in `agent/skills/<skill>/SKILL.md`. Frontmatter controls inclusion; loaded at startup.
- **Project-wide**: `README.md` (user-facing quickstart), `SECURITY.md` (threat model, network exposure, log privacy), `CONTRIBUTING.md` (branching, sign-off, release flow).
