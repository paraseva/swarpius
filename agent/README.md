# Swarpius Agent

This is a Python backend for Swarpius™ — an LLM-driven assistant for Roon. It orchestrates multi-step tool calling, manages Roon library navigation and playback, and serves a WebSocket API consumed by the web client.

## What it does

- Runs a native tool-calling loop: the LLM decides which tools to call, the agent executes them, and the results go back into the conversation until the LLM produces a text response
- Executes tools for web search, Roon library search, playback control, zone status, zone configuration, and search result management
- Handles interrupt arbitration when a new message arrives while one is in-flight
- Logs every request with full pipeline detail for post-hoc diagnosis
- Streams diagnostics, usage and analysis metrics, and live Roon events over WebSocket channels
- Per-model tuning (step limits, temperature, top_p, provider-specific generation params) via `model_profiles.yaml`

## Prerequisites

- Python 3.13
- A Roon Core instance on the same network (auto-discovered or explicit URL)
- An LLM (Anthropic, OpenAI, Ollama, or any LiteLLM-supported provider)
- *(Strongly recommended)* A web-search backend — Brave (free tier, easiest signup), Tavily, or self-hosted SearXNG. See the root README's [Web search](../README.md#web-search-strongly-recommended) section.

## Environment

Copy from `.env.template`:

```bash
cp .env.template .env
```

### Required variables

| Variable | Description |
|---|---|
| `LLM_MODEL` | Default model in `provider/model` format (e.g. `anthropic/claude-sonnet-4-6`) — see the root README's [Supported LLM providers](../README.md#supported-llm-providers) for tested options |
| `LLM_API_KEY_<PROVIDER>` | API key per provider (e.g. `LLM_API_KEY_ANTHROPIC`, `LLM_API_KEY_OPENAI`). Local providers like Ollama need no key. |

### Web search

Web search is configured separately and **strongly recommended** — without it, requests that require external information (such as "play 3 top UK hits from the 1980s") may cause the LLM to take an alternative approach, such as fall back on its own knowledge, or report that it can't complete the task. There are currently three supported options:

| Variable | Backend | Notes |
|---|---|---|
| `BRAVE_API_KEY` | [Brave](https://brave.com/search/api/) | Recommended for new users — free tier covers casual use, signup is email + key |
| `TAVILY_API_KEY` | [Tavily](https://tavily.com/) | Managed alternative |
| `SEARXNG_URL` | Self-hosted SearXNG | Bundled in the Docker compose stack under `--profile search` |

`WEB_SEARCH_PROVIDER` is required to enable web search — set it to `brave`, `tavily`, `searxng`, or `none`. Each provider requires its corresponding credential above; if the credential is missing, the agent disables web search and warns at startup. Unset (or `none`) → web search disabled cleanly. Docker compose users with `--profile search` get `SEARXNG_URL` injected automatically; users running from source set it themselves. The startup log shows the chosen backend on every boot. See the root README's [Web search](../README.md#web-search-strongly-recommended) section for details.

### Per-agent model overrides

The coordinator always uses `LLM_MODEL`. The lightweight sub-agents below fall back to it but can be overridden — typically with a cheaper / faster model since their jobs are classification, not full reasoning.

| Variable | Agent | Notes |
|---|---|---|
| `LLM_MODEL_ARBITER` | Interrupt Arbiter | Lightweight classification — can use a smaller model |
| `LLM_MODEL_DIAGNOSTIC` | Diagnostic Agent | Conversation classification — can use a smaller model |
| `LLM_MODEL_ANALYSER` | Analyser Agent | Conversation analysis — would benefit from the strongest models |

### Model profiles

LLM tuning (temperature, sampling params, coordinator step caps, provider-specific flags) is configured in `model_profiles.yaml`. Profiles are matched by regex against the full `provider/model` string. See [Model Profiles](../docs/model-profiles.md) for details and examples.

### Other optional variables

| Variable | Default | Description |
|---|---|---|
| `ENABLE_DIAGNOSTIC_AGENT` | `false` | LLM-driven conversation classification for log grouping |
| `ENABLE_INTERRUPT_ARBITER` | `false` | LLM call decides whether a new in-flight request queues, interrupts-and-replaces, or interrupts-only. Set to `true` to enable; default is plain queue. |
| `ENABLE_PROMPT_CACHING` | `true` | Cache-control markers for Anthropic, Gemini, and Vertex models (OpenAI / DeepSeek cache automatically, so no effect there) |
| `ROON_CORE_URL` | (auto-discover) | Explicit Roon Core address (e.g. `http://192.168.1.100:9330`) |
| `ROON_CORE_NAME` |—| Roon Core name to pair with when multiple Cores are on the network (matches Settings > General > ROON SERVER). Ignored if only one Core is discovered or `ROON_CORE_URL` is set. |
| `ROON_PROFILE_NAME` |—| Roon profile to authenticate as. Note: the Roon API doesn't currently set this properly |
| `DEFAULT_ROON_ZONE` | (first zone the Core reports) | Default Roon zone name used for actions when the user doesn't specify one |
| `LLM_PERSONA` |—| Persona for Swarpius to adopt — character name (e.g. `Peter Griffin`) or personality description (e.g. `Funny and sarcastic`) |
| `LOG_FILE` | `data/logs/swarpius.log` (all modes) | Path to the agent log file (rotates at 10MB, keeps 3 backups). Unset → the default path is used in every mode. CLI mode routes INFO to the file so the terminal stays clean; WS / Docker keep stderr alongside the file. |
| `LOG_RETENTION_DAYS` | `7` | Request log retention in days |
| `CONVERSATION_IDLE_TIMEOUT_SECONDS` | `300` | Idle gap that starts a new conversation group |
| `PARALLEL_TOOLS` | `false` | Run parallelisable tool calls concurrently within a step |
| `ROON_MAX_PARALLEL` | `5` | Max concurrent Roon operations per step; parallel-safe calls run in batches of this size. Set `0` (or any value `< 1`) for unlimited. Default keeps Roon Cores from dropping or stalling responses on large multi-track requests. |
| `ROON_SEARCH_RETRY_LIMIT` | `2` | Max retries when Roon search returns a transient empty result |
| `ROON_SEARCH_RETRY_DELAY` | `1.0` | Seconds between search retries |
| `TTS_URL` |—| F5-TTS server address (scheme-less `host:port`, TCP). Drives both CLI-mode speech and the agent's TTS proxy for the web client. Leave unset to disable. |

### Advanced tuning

Safe defaults — most users do not need to set these. See `.env.template` for the longer rationale on each.

| Variable | Default | Description |
|---|---|---|
| `ROON_STOP_MARKER_TITLE` | `Swarpius_Stop_Playback` | Title of the silent audio file in the Roon library that the `stop` transport action plays to end playback and clear the queue. See [Known Limitations](../docs/known-limitations.md) for setup. |
| `DISABLE_SIMULATED_STOP` | `false` | Hard opt-out for the simulated-stop feature. When `true`, stop requests just pause and the web client hides the stop button. |
| `CONVERSATION_HISTORY_MAX_TURNS` | `5` | Recent user/agent turns retained in the conversation-history context provider |
| `SEARCH_HISTORY_MAX_ENTRIES` | `5` | Recent search-result handles retained in the search-history context provider |
| `EXECUTION_TRACE_MAX_LENGTH` | `10` | Recent tool calls retained in the execution-trace context provider |
| `RESULT_STORE_MAX_ENTRIES` | `50` | Cached search-result entries kept in the result store (LRU-evicted) |
| `IMAGE_CACHE_MAX_ENTRIES` | `200` | Zone-artwork cache size at the resolutions the frontend has requested (LRU-evicted) |
| `ENABLE_PASSIVE_ANALYSER` | `false` | Master toggle for the LLM-driven conversation-quality analyser. Off by default — analysis costs LLM tokens. When `true`, the agent runs a background scan loop on startup. The "Scan & Analyse" / "Re-Analyse" buttons in the analysis browser also work with this off; they perform one-shot analysis under the same scan lock. |
| `ANALYSER_INTERVAL_MINUTES` | `30` | Background loop scan period (minutes). Only consulted when `ENABLE_PASSIVE_ANALYSER=true`. |
| `ANALYSER_STALENESS_MINUTES` | `60` | How long a conversation must be idle before the background loop considers it stable enough to analyse. |
| `ANALYSER_BATCH_SIZE` | `5` | Conversations batched per analyser LLM call. Honoured by the background loop, the on-demand "Scan & Analyse" button, and the CLI analyser. |
| `ANALYSIS_HISTORY_MAX_ENTRIES` | `20` | Max versions of `analysis-history.yaml` retained per conversation before older entries rotate out |
| `SWARPIUS_DATA_DIR` | `agent/data/` | Location for mutable data (logs, config, message DB, analysis results). Override for Docker / self-hosted deployments. |
| `SWARPIUS_WS_HOST` | `127.0.0.1` | WebSocket server bind host (`--ws` mode only). Loopback by default — set `0.0.0.0` to expose to the LAN (read [SECURITY](../SECURITY.md) first). Docker sets `0.0.0.0` in the container and gates host exposure via `*_BIND_IP`. |
| `SWARPIUS_WS_PORT` | `8080` | WebSocket server bind port (`--ws` mode only) |

## Install

Three requirements files, named for *what* they cover rather
than *how* you deploy:

| File | Includes | When to use |
|---|---|---|
| `requirements.txt` | Core runtime + PyAudio + numpy | **Default.** Local CLI use, dev workstation, anywhere you run `python3 swarpius.py` directly. The audio libs are needed for CLI-mode TTS playback (`tts/tts.py` decodes PCM frames from the TTS server and plays them via PyAudio on the local sound device). |
| `requirements-server.txt` | Core runtime only — no audio libs | Containers, daemons, headless servers running `--ws` mode. The browser handles TTS playback in WS mode, so the server itself never imports PyAudio. Skipping it avoids the `portaudio` system-library dependency. The agent Dockerfile uses this file. |
| `requirements-dev.txt` | Everything in `requirements.txt` + `pytest` + `ruff` | Contributors / dev work. Pulls in audio transitively, so dev installs match the default user experience. |

The `-server` version covers any non-CLI deployment: Docker today, a future
systemd unit, anything where the agent runs as a daemon and the
audio path doesn't apply.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt   # or -server / -dev
```

### PyAudio system dependency

The default `requirements.txt` install pulls PyAudio, which
needs the `portaudio` system library at build *and* runtime
(it's a C extension that links to `libportaudio` on import).
Per-platform install:

- **Debian/Ubuntu:** `sudo apt install portaudio19-dev`
- **Fedora/RHEL:** `sudo dnf install portaudio-devel`
- **macOS:** `brew install portaudio`
- **Windows:** precompiled wheels usually work via
  `pip install pyaudio` directly; if not, fall back to
  `pip install pipwin && pipwin install pyaudio`.

If PyAudio install fails and you only need WS / server mode,
install `requirements-server.txt` instead — that path skips the
audio libs entirely.

### WSL contributors: the `./dev` wrapper

If you develop under WSL, use the `./dev` wrapper at the agent
root rather than invoking `pytest` / `ruff` / `python` directly:

```bash
./dev pytest            # full offline test suite
./dev ruff check .      # lint
./dev python <script>   # ad-hoc script
```

It activates a separate `.venv-wsl` venv (distinct from `.venv`
to avoid clashing with a Windows-side venv when the same repo is
also opened from Windows), and exports a `TMPDIR` on the WSL
filesystem so pytest's capture plugin doesn't fall over on
Windows-mounted paths.

Set up the WSL venv once with:

```bash
python3 -m venv .venv-wsl
.venv-wsl/bin/pip install -r requirements-dev.txt
```

Linux / macOS contributors can ignore the wrapper and use the
standard `.venv` flow above.

## Run

CLI mode (interactive terminal — the default):

```bash
python3 swarpius.py
```

WebSocket mode (for the web client and Docker deployments):

```bash
python3 swarpius.py --ws
```

The WebSocket bind defaults to `127.0.0.1:8080` (loopback) — only the
same machine can reach it. Set `SWARPIUS_WS_HOST=0.0.0.0` to expose it
to the LAN (read [SECURITY.md](../SECURITY.md) first). Inside Docker the
container binds `0.0.0.0` (set in the compose file — Docker's
port-publish needs it), and host exposure is modified separately via
`*_BIND_IP` in `docker-compose.yml` (defaults to `127.0.0.1`).

See `--help` for all flags. Useful CLI-mode flags:

| Flag | Purpose |
|---|---|
| `--show-request-ids` | Display request IDs (`rq-cNN-NNNN`) on the user-input and response panels. Off by default; enable to grep `data/logs/conversation/<date>/<cNN>/<rid>/` when debugging. |
| `--keep-history` | **WS only** — passing this without `--ws` is a hard error. Retains chat history from the previous WS session (rendered greyed-out in the frontend). For CLI use the readline history (up-arrow / reverse-i-search) to recall prior input. |

### What CLI mode looks like

After startup, the terminal shows a clean banner with the resolved configuration (Roon Core, default zone, coordinator/arbiter/diagnostic models, web-search backend, TTS, prompt caching, parallel tools, log file path). Each request is then framed as:

- A spinner that updates per loop step (`Thinking…` / `Searching library…` / `Controlling playback…`).
- The agent's response in a green-bordered panel.
- A dim per-request telemetry line: `1,234 in · 84 out · 800 cached · $0.0034 · 2 steps · 1.4s`.
- A dim running session total: `session: 4,134 in · 254 out · …`.

### Prompt commands and shortcuts

- `/exit`: quit cleanly.
- `/usage`: print the running session totals plus per-request averages on demand.
- **Ctrl+C at the prompt**: first press clears the line and prints `(press Ctrl+C again to exit, or /exit)`; second consecutive press quits cleanly. Any successful command receipt resets the armed state.
- **Ctrl+C during a request**: first press cancels the in-flight request gracefully via the existing cancel-event infrastructure; second press exits the program. The daemon worker dies with the process.
- **Ctrl+D (Linux/macOS)** or **Ctrl+Z + Enter (Windows)**: exit cleanly.
- **Up-arrow / reverse-i-search**: readline history works on Linux/macOS out of the box; Windows users need `pip install pyreadline3` (history persists at `data/cli_history`, capped at 1000 entries).

### Logging in CLI mode

INFO log lines are routed to a file by default so the terminal stays clean. If `LOG_FILE` is unset, the file defaults to `data/logs/swarpius.log` (10 MB rotating, 3 backups). Errors and warnings still appear on stderr. The banner shows the active log file path.

---

> The remainder of this README is **for contributors and operators wanting a deeper view** of how the agent works internally. Users following the root README's Quickstart don't need anything below this line.

---

## Request pipeline

Every user message goes through a single pipeline in `app/coordinator/request_flow.py`:

1. **Conversation classification**—if the diagnostic agent is enabled, a lightweight LLM call classifies the input into a conversation thread (reuse existing or start new). Falls back to timeout-based grouping if disabled.

2. **System prompt assembly**—a single system prompt is built from the base prompt plus dynamic context sections (date/time, conversation history, execution trace, search history, skill definitions).

3. **Tool-calling loop**—the message list is sent to the LLM via LiteLLM with the full tool registry. The LLM natively decides whether to call a tool or produce a text response. There is no separate intent router—the LLM selects tools directly. When `PARALLEL_TOOLS` is enabled, independent tool calls within a step execute concurrently via `asyncio.gather`. Within a request, tool call and result messages accumulate in the conversation window.

4. **Tool execution**—`ToolRegistry.execute()` dispatches each tool call by name, deserialising arguments into the Pydantic input model. Results are optionally compacted before going back into the conversation.

5. **Loop termination**—the loop ends when the LLM produces a text response, the step limit is hit (default 12), or on error. A soft nudge is injected at step 8 to encourage wrapping up.

6. **Response extraction**—the final text is sanitised and emitted as the chat response.

Cross-request context is maintained through providers in the system prompt: execution trace, search history, and conversation history.

For how tools are defined, registered, and documented (including SKILL.md format and adding new tools), see [Tool System](../docs/tool-system.md).

## Request logging

Every request is assigned a sequential ID in the format `rq-cNN-NNNN` (e.g. `rq-c01-0003`), where `cNN` is a conversation identifier and `NNNN` is a monotonic request sequence. Two log systems operate in parallel:

- **Conversation logs**: `logs/conversation/<date>/<cNN>/<request-id>/`
  - `request.json`: user input, request ID, timestamp
  - `coordinator_steps/step_NN.yaml`: LLM input/output per step
  - `tool_executions/NN_<skill>.json`: full tool I/O and timing
  - `prompts/`: system prompts and context provider content
  - `outcome.json`: final status, chat response, total timing
  - `events.jsonl`: chronological stream of all WS emissions

- **Server logs**: `logs/server/<date>/<cNN>/<request-id>/server.yaml`
  - Detailed YAML trace of browse/action operations within Roon API calls

Request IDs are displayed in the frontend and can be clicked to copy. Logs are retained for 7 days by default.

## WebSocket channels

Messages are JSON `{"channel": "<name>", "payload": ...}`.

| Channel | Direction | Purpose |
|---|---|---|
| `chat` | Both | User messages in, agent responses out |
| `agent-outputs` | Out | Diagnostic event stream |
| `tool-outputs` | Out | Tool input/output pairs |
| `errors` | Out | Error events |
| `usage-metrics` | Out | Token accounting |
| `llm-diagnostics` | Out | LLM call lifecycle events |
| `rate-limit` | Out | Rate-limit state for frontend banners |
| `zone-snapshots` | Out | Full Roon zone state, re-emitted on any change |
| `roon-image-request/response` | Both | Artwork image fetch |
| `roon-control-request/response` | Both | Transport controls from frontend |
| `session-control-request/response` | Both | Interrupt/cancel controls |

This table covers the main channels and isn't exhaustive — see `app/constants.py` for the full list.

## Project layout

For the module-by-module map (request flow, tool loop, Roon API surface, etc.) see [Architecture](../docs/architecture.md). The full source tree is browsable under `agent/`.

## Testing and linting

The dev workflow — running `pytest` (offline + live-Roon), `ruff check`, and the WSL `./dev` wrapper — is documented in [CONTRIBUTING.md](../CONTRIBUTING.md). For a quick smoke test:

```bash
python3 -m pytest        # offline tests
ruff check .             # lint
```

## Docker

From the monorepo root:

```bash
docker compose up -d swarpius-agent
```

The agent image uses a multi-stage build with `python:3.13.13-slim-bookworm` as the runtime base.
