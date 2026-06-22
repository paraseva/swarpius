from __future__ import annotations

import re

CHANNEL_CHAT = "chat"
CHANNEL_AGENT_OUTPUTS = "agent-outputs"
CHANNEL_TOOL_OUTPUTS = "tool-outputs"
CHANNEL_ZONE_SNAPSHOTS = "zone-snapshots"
CHANNEL_USAGE_METRICS = "usage-metrics"
CHANNEL_LLM_DIAGNOSTICS = "llm-diagnostics"
CHANNEL_IMAGE_REQUEST = "roon-image-request"
CHANNEL_IMAGE_RESPONSE = "roon-image-response"
CHANNEL_ROON_CONTROL_REQUEST = "roon-control-request"
CHANNEL_ROON_CONTROL_RESPONSE = "roon-control-response"
CHANNEL_ROON_EXPLORER_REQUEST = "roon-explorer-request"
CHANNEL_ROON_EXPLORER_RESPONSE = "roon-explorer-response"
CHANNEL_FEATURE_AVAILABILITY = "feature-availability"
# User-triggered re-check (sent by the web client when a "waiting"
# stop button is clicked). The agent runs the StopMarkerCoordinator's
# init walk and the result lands on CHANNEL_FEATURE_AVAILABILITY via
# the coordinator's broadcast — there's no dedicated response channel.
CHANNEL_FEATURE_VERIFY_REQUEST = "feature-verify-request"
# Bundle-only: the web client asks the agent to open the stop-marker
# folder in the OS file manager. Fire-and-forget — no response channel;
# refused server-side outside a desktop bundle.
CHANNEL_OPEN_DATA_FOLDER_REQUEST = "open-data-folder-request"
CHANNEL_SESSION_CONTROL_REQUEST = "session-control-request"
CHANNEL_SESSION_CONTROL_RESPONSE = "session-control-response"
CHANNEL_CLEAR_CONVERSATION_REQUEST = "clear-conversation-request"
CHANNEL_CLEAR_CONVERSATION_RESPONSE = "clear-conversation-response"
CHANNEL_CLEAR_LISTENING_HISTORY_REQUEST = "clear-listening-history-request"
CHANNEL_CLEAR_LISTENING_HISTORY_RESPONSE = "clear-listening-history-response"
# History lazy-load: the request is fire-and-forget (the server replies by
# sending the day's messages on their normal channels, tagged historical);
# the cursor is a passive signal carrying whether older history exists.
CHANNEL_HISTORY_REQUEST = "history-request"
CHANNEL_HISTORY_CURSOR = "history-cursor"
CHANNEL_RATE_LIMIT = "rate-limit"
CHANNEL_ERRORS = "errors"
CHANNEL_ANALYSIS_LIST_REQUEST = "analysis-list-request"
CHANNEL_ANALYSIS_LIST_RESPONSE = "analysis-list-response"
CHANNEL_ANALYSIS_DETAIL_REQUEST = "analysis-detail-request"
CHANNEL_ANALYSIS_DETAIL_RESPONSE = "analysis-detail-response"
CHANNEL_ANALYSIS_RUN_REQUEST = "analysis-run-request"
CHANNEL_ANALYSIS_RUN_RESPONSE = "analysis-run-response"
CHANNEL_ANALYSIS_METRICS_REQUEST = "analysis-metrics-request"
CHANNEL_ANALYSIS_METRICS_RESPONSE = "analysis-metrics-response"
CHANNEL_ANALYSIS_REQUEST_LOGS_REQUEST = "analysis-request-logs-request"
CHANNEL_ANALYSIS_REQUEST_LOGS_RESPONSE = "analysis-request-logs-response"
CHANNEL_ANALYSIS_RESULT_HANDLE_REQUEST = "analysis-result-handle-request"
CHANNEL_ANALYSIS_RESULT_HANDLE_RESPONSE = "analysis-result-handle-response"
CHANNEL_ANALYSIS_FEEDBACK_REQUEST = "analysis-feedback-request"
CHANNEL_ANALYSIS_FEEDBACK_RESPONSE = "analysis-feedback-response"
CHANNEL_ANALYSIS_UPDATE = "analysis-update"
CHANNEL_DEFAULT_ZONE_UPDATE = "default-zone-update"
CHANNEL_QUEUE_UPDATES = "queue-updates"
# Roon Core connection health — {"state": "connected"|"lost"}. Emitted on
# transition by the background health watcher, and once per WS connect so
# a client joining mid-outage learns the current state.
CHANNEL_ROON_CORE_STATUS = "roon-core-status"

# Settings UI channels — drive the in-browser configuration page.
# Read returns current .env contents; save persists updates back to
# the same file; reload re-reads after out-of-band edits. The Settings
# UI gates the chat surface on `config_complete` from the
# feature-availability payload.
CHANNEL_SETTINGS_READ_REQUEST = "settings-read-request"
CHANNEL_SETTINGS_READ_RESPONSE = "settings-read-response"
CHANNEL_SETTINGS_SAVE_REQUEST = "settings-save-request"
CHANNEL_SETTINGS_SAVE_RESPONSE = "settings-save-response"
CHANNEL_SETTINGS_RELOAD_REQUEST = "settings-reload-request"
CHANNEL_SETTINGS_RELOAD_RESPONSE = "settings-reload-response"
CHANNEL_SETTINGS_TEST_REQUEST = "settings-test-request"
CHANNEL_SETTINGS_TEST_RESPONSE = "settings-test-response"
# Boot-time / Save & Validate LLM config validation. Outbound only:
# server emits on state transitions (OPEN → VALIDATING → PASSED|FAILED)
# and on WS connect (snapshot). Save & Validate triggers a fresh run
# via the existing settings-save-request flow.
CHANNEL_VALIDATION_STATUS = "validation-status"

# WebSocket close code signalling that another client with a different
# session_id has taken over the single-session slot. The old client
# should display a "taken over" overlay and not auto-reconnect.
CLOSE_CODE_SESSION_TAKEOVER = 4001

CHAT_PANEL_AGENTS = {"Coordinator"}
MAX_COORDINATOR_STEPS = 20
TARGETED_TOOL_MAX_ATTEMPTS = 2
TARGETED_TOOL_RETRY_BASE_DELAY_SECONDS = 0.2
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY_SECONDS = 30
CONTEXT_MAX_LIST_ITEMS = 8
CONTEXT_MAX_STRING_LENGTH = 180
FULL_RESULTS_WINDOW = 1
# Env-driven configuration (CONVERSATION_HISTORY_MAX_TURNS,
# RESULT_STORE_MAX_ENTRIES, IMAGE_CACHE_MAX_ENTRIES, etc.) lives on
# ``app.settings.Settings``. Read via ``get_settings()`` at the point
# of use so the locked-at-startup invariant holds.
RESULT_HANDLE_REGEX = re.compile(r"\b(?:res|que)_\d{5}\b", re.IGNORECASE)

# WebSocket back-pressure / DoS limits. Single-session hygiene means
# only one client per agent under normal use; these are abuse caps,
# not concurrency caps.
PENDING_MESSAGES_MAXLEN = 20
WS_MAX_FRAME_SIZE = 64 * 1024  # 64 KB per incoming frame
WS_MAX_QUEUE_SIZE = 32  # incoming frames buffered before back-pressure
