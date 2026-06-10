# Agent Test Suite

Unit tests and live integration tests for browse sessions, reference resolution, request flow, tools, and runtime behaviour.

## Test modules

### Offline tests (no external dependencies)

- `test_browse_session.py`: `BrowseSessionManager` — session keys, ref minting/lookup/eviction, `is_key_live`, depth tracking
- `test_reference_resolution.py`: `resolve_reference`, `compile_output`, `get_media_actions`, cross-search reference flows (uses `FakeRoonApi`)
- `test_request_flow_result_handle_guards.py`: request-flow guard behaviour — result handle normalisation, fallback paths, planner guard logic
- `test_result_fetch.py`: result cache retrieval behaviour
- `test_roon_action.py`: action schema validation, execution order, playback settings, advanced controls
- `test_roon_connection_init.py`: Roon connection discovery/auth/bootstrap paths
- `test_roon_status.py`: status tool output behaviour
- `test_runtime_result_store_integration.py`: runtime integration — prompt context, active skill instructions, result store propagation
- `test_agent_skills_loader.py`: `SKILL.md` loading, frontmatter parsing, formatting
- `test_usage_metrics.py`: prompt diagnostics and usage metric aggregation

### Live Roon tests (require a running Roon Core)

- `test_roon_browse_integration.py`: live browse session behaviour — connection, search, cross-search resolution, batched actions, and diagnostic tests documenting Roon API behaviour (`item_key` context sensitivity, `pop_all` vs `pop_levels`, `multi_session_key` isolation)

## Running tests

From `agent/` (with the venv activated). WSL contributors can prefix each command with the `./dev` wrapper instead (see [`../README.md`](../README.md)):

```bash
# Run offline tests only (default — live tests are excluded)
pytest

# Run live Roon tests only (requires Roon Core; reads ROON_CORE_URL and
# DEFAULT_ROON_ZONE from agent/.env automatically)
pytest -m live_roon

# Run everything
pytest -m ""

# Run a single module
pytest tests/test_reference_resolution.py

# Run a single test
pytest tests/test_reference_resolution.py::TestCrossSearchReferences::test_three_searches_then_batched_resolve

# Verbose with log output (useful for diagnostic tests)
pytest -m live_roon -v --log-cli-level=INFO
```

## Environment

`conftest.py` loads test config in this order: explicit env > `agent/.env.test` > `agent/.env`. The first source that defines a variable wins.

For most users `agent/.env` is enough — no extra setup needed. If you want test-only overrides (e.g. point live tests at a different Roon Core, or override the per-library `ROON_TEST_*` defaults below), copy the template:

```bash
cp .env.test.template .env.test
```

then uncomment and edit only the lines you need. `.env.test` is gitignored.

Live Roon tests use the `live_roon` pytest marker and are excluded by default in `pytest.ini`. The test file uses a single shared `RoonConnection` across all test classes to avoid Roon's single-connection-per-extension limitation.

### Library-specific overrides for live tests

Live tests search a real Roon library. Every search term, artist, album, and playlist name is behind an env var with **no default** — set each to something in your library (the examples below are common picks). Any var you leave unset makes its dependent tests `skipTest()` with a message naming the var. Set these in `agent/.env.test` (preferred — keeps test-only config out of `.env`) or `agent/.env` to match what *your* library contains:

| Env var | Example | Used for |
|---|---|---|
| `ROON_TEST_SEARCH_A` | `Beatles` | General-purpose search (most tests) |
| `ROON_TEST_SEARCH_B` | `Mozart` | Second search for cross-search and parallel tests |
| `ROON_TEST_SEARCH_C` | `Adele` | Third search for three-way cross-session tests |
| `ROON_TEST_PLAYLIST` | `Favourites` | Playlist name for playlist-action tests |
| `ROON_TEST_EXPAND_PLAYLIST` | *same as* `ROON_TEST_PLAYLIST` | Alternative playlist for the Shuffle-expand test |
| `ROON_TEST_ALBUM` | `Thriller` | Album name for album-action tests |
| `ROON_TEST_AMBIGUOUS_SEARCH` | `Thriller Michael Jackson` | Search that returns both an album *and* a same-titled track |
| `ROON_TEST_AMBIGUOUS_TITLE` | `Thriller` | The shared title between the album and the track above |
| `ROON_TEST_ARTIST_A` | `Beatles` | Primary artist for Shuffle-rejection tests |
| `ROON_TEST_ARTIST_B` | `Rolling Stones` | Secondary artist for multi-artist Shuffle tests |

All tests that depend on these skip cleanly (not fail) when a var is unset or its value isn't found in your library — so a first run with nothing set will tell you exactly which vars you need to provide.
