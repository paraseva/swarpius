# Swarpius data directory

Holds all per-installation runtime state — config the agent learns, request logs,
the message database, and CLI history. Override the location with the
`SWARPIUS_DATA_DIR` environment variable; otherwise everything below is relative
to this folder. Subfolders and files are created on first use.

Anything here is safe to delete — Swarpius will recreate what it needs on the
next run.

## Contents

- `config/`: Roon zone aliases, group names, and remembered zone-group mappings.
- `logs/conversation/<date>/<cNN>/<request-id>/`: per-request trace: user input,
  LLM steps, tool calls, final response.
- `logs/server/<date>/<cNN>/<request-id>/server.yaml`: detailed YAML trace of
  Roon browse/action operations.
- `logs/swarpius.log`: INFO-level server log, written in every mode (CLI, WS source, WS bundle, Docker). Override the path with `LOG_FILE`.
- `analysis/`: metrics (`metrics.jsonl`) and feedback archive.
- `messages.db` (+ `-wal`, `-shm`): SQLite store of messages across sessions.
- `play_history.json`: per-zone "what just played" cache.
- `cli_history`: readline command history for CLI mode.

Conversation and server logs are pruned after 7 days by default
(configurable via `LOG_RETENTION_DAYS`).
