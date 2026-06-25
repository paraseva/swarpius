# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.1] - 2026-06-26

### Added

- `SWARPIUS_TIMEZONE` (`agent/.env`): the IANA timezone (e.g. `Europe/London`) used to group conversations and name log directories by day. Defaults to the system local timezone, which is correct for source and installer runs. Docker users should set it, because a container's clock defaults to UTC — without it, requests might be grouped into the wrong day.

### Fixed

- Conversation grouping, day boundaries, and timestamps now use local time, rather than UTC. On systems where local time differs from UTC, this previously caused conversations to be grouped under the wrong day, the per-day conversation numbering not to reset at local midnight, and some timestamps (including listening history) to appear shifted from local time. The local timezone is taken from the system by default; in Docker, set `SWARPIUS_TIMEZONE` (see above). Conversations recorded before this fix are not re-dated automatically. They age out under the retention windows (`CHAT_HISTORY_RETENTION_DAYS`, `LOG_RETENTION_DAYS`), or can be removed immediately with Settings → "Privacy & Data" → "Clear conversation history".
- "Clear conversation history" (Settings → "Privacy & Data") previously removed only the in-app transcript, leaving the server-side conversation and server log directories (`logs/conversation/` and `logs/server/` under the data directory) on disk — the most detailed record of a conversation, including your inputs, the assistant's responses, prompts, and tool input/output. It now deletes those too. The cost dashboard's usage history and your listening history (cleared separately) are unaffected.
- In Developer Mode, the Session Requests panel could drop or conflate requests that shared an identifier across different days (request identifiers reset each day). Each request is now tracked per day, so same-numbered requests on different days stay distinct.
- The header and close button are now consistent across the full-screen views (Costs, Conversation Analysis, Settings, and Live Diagnostics): the close icon is identical everywhere, and the Settings and Live Diagnostics headers expand to a second row when the window is too narrow for their controls, rather than clipping them.

## [1.1.0] - 2026-06-25

### Added

- Chat history, conversation memory, and Roon reference state now persist across a restart. After the agent restarts, the conversation continues as before: the assistant still remembers what was said, previously found search results and queue items still resolve, and the default-zone choice is retained.
- Browse your full chat history in the web client: scroll up to load earlier days one at a time, or jump straight to any date with the calendar picker in the chat header. Day separators mark each day, and only the most recent day loads on open so the interface stays fast.
- A scroll-to-bottom button in the chat and the Agents, Tools, and Errors diagnostics panels: when you scroll up into earlier messages, a button appears to jump back to the latest, and it highlights when new messages arrive while you are scrolled away.
- The assistant can answer questions about what you have listened to (for example, "what did I play last Tuesday?"), backed by a listening-history record of recently played tracks.
- A Cost dashboard in the web client (always available, alongside Settings — not behind Developer Mode), tracking LLM spend across every agent: the coordinator, the lightweight sub-agents, and the conversation analyser. It shows a cost-and-tokens trend over time, breakdowns by agent and by model, and mean cost per request by complexity, filterable by date range, agent, and model. Cost history is kept indefinitely (it is not subject to the diagnostics retention window). The command-line `/usage` view gains a matching all-time cost summary.
- A "Privacy & Data" tab in Settings with controls to clear your conversation history or your listening history.
- Configurable retention windows for persisted data, set in `agent/.env`: `CHAT_HISTORY_RETENTION_DAYS` (default 90), `DIAGNOSTICS_RETENTION_DAYS` (default 30), and `LISTENING_HISTORY_RETENTION_DAYS` (default 365). Set any to `0` to keep that data indefinitely.
- Developer Mode: clicking a request-ID badge now focuses that request across every open request-aware panel (chat, Agents, Tools, Errors, Session Requests) at once, scrolling each to it; the badge also keeps a copy-to-clipboard control.

### Changed

- Chat history is now retained across restarts by default and is no longer shown greyed-out as a "previous session". Previously, history was cleared on every startup unless `--keep-history` was passed.
- Message timestamps in the chat now reflect when each message was actually sent or received, rather than when it was committed to history.

### Removed

- The `--keep-history` command-line flag has been removed; history retention is now the default behaviour. Remove the flag from any custom launch command (the bundled Docker Compose file has been updated).
- The `DEFAULT_ROON_ZONE` environment variable has been removed. The default zone is now chosen automatically (the first zone the Core reports) and remembered across restarts once changed; set it from the web client's zone controls. Remove `DEFAULT_ROON_ZONE` from any custom `.env`.

### Fixed

- Asking the assistant to look at several albums (or other items) at once — for example, drilling into multiple albums to find a particular track — could return the same track list for different albums, and then play or queue the wrong track. Concurrent library browsing now keeps each item's results separate, so both the listings and the chosen track are correct.

## [1.0.1] - 2026-06-18

### Fixed

- Requests to play, queue, or shuffle a track that the library matches to an album instead — for example, a song that shares its title with a compilation — are now handled correctly. The mismatch is detected and the intended track recovered, rather than returning an unhelpful error or, when shuffling, adding the album's entire tracklist (including duplicate tracks) to the queue. Other items in the same request are unaffected.

## [1.0.0] - 2026-06-11

Initial public release.

### Added

- Natural-language control of a Roon music system: play, pause, queue, skip, seek, shuffle, and transfer playback between zones.
- Multi-step library navigation: search artists, albums, tracks, and playlists, and drill into results across successive steps.
- Multi-zone support with name and alias resolution, fuzzy matching, and zone grouping.
- Optional web search (Brave, Tavily, or self-hosted SearXNG) for information the Roon library cannot answer, chained into library searches where needed.
- Optional text-to-speech voice output via F5-TTS.
- Browser interface with live zone status, artwork, and transport controls, plus a Developer Mode exposing request tracing, LLM-call diagnostics, token-usage tracking, and post-hoc conversation analysis.
- Command-line mode for terminal-only use.
- Provider-neutral model selection through LiteLLM (Anthropic, OpenAI, Gemini, Ollama, LM Studio, and others).
- Distribution as signed installers for Windows, macOS, and Linux, via Docker Compose, or run from source.
