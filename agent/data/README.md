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
- `messages.db` (+ `-wal`, `-shm`): the persistent state store (schema-versioned
  SQLite). Holds the chat transcript, the assistant's working memory (recent
  conversation turns, execution trace, cached search results), Roon browse/queue
  references, the conversation-tracker state, the persisted default zone, and the
  listening-history record. This is what lets a restart resume where you left
  off. Clear it from the web client (Settings → Privacy & Data), or delete the
  file (with the agent stopped) to wipe everything.
- `play_history.json`: per-zone "what just played" cache.
- `cli_history`: readline command history for CLI mode.

Conversation and server **logs** are pruned after 7 days by default
(`LOG_RETENTION_DAYS`). Persisted **state** in `messages.db` is pruned on its own
schedule: chat transcript + working memory after `CHAT_HISTORY_RETENTION_DAYS`
(default 90), diagnostics after `DIAGNOSTICS_RETENTION_DAYS` (default 30), and
listening history after `LISTENING_HISTORY_RETENTION_DAYS` (default 365). Set any
to `0` to keep that data indefinitely.
