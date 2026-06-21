# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Chat history, conversation memory, and Roon reference state now persist across a restart. After the agent restarts, the conversation continues as before: the assistant still remembers what was said, previously found search results and queue items still resolve, and a runtime default-zone choice is retained.

### Changed

- Chat history is now retained across restarts by default and is no longer shown greyed-out as a "previous session". Previously, history was cleared on every startup unless `--keep-history` was passed.

### Removed

- The `--keep-history` command-line flag has been removed; history retention is now the default behaviour. Remove the flag from any custom launch command (the bundled Docker Compose file has been updated).

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
