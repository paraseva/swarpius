"""WebSocket request handlers for the Settings UI.

Each handler takes a payload dict and returns a response dict — the
WS dispatcher in ``app/websocket_flow.py`` wraps that into the
response channel and emits it.

Channels:

- ``settings-read-request``   → ``settings-read-response``
- ``settings-save-request``   → ``settings-save-response``
- ``settings-reload-request`` → ``settings-reload-response``

The provider-specific test endpoint (``settings-test-*``) lives in
``settings_test_endpoint.py``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

from app.settings import (
    required_config_missing,
    reset_settings_for_tests,
)
from app.settings.env_file import (
    env_editable,
    read_managed_env,
    reload_env_into_process,
    resolve_env_path_for_display,
    write_env_file,
)

log = logging.getLogger(__name__)


# Env-var names whose values are secrets. The frontend uses this list
# to render those fields as password inputs with a show/hide toggle.
# Add new providers here when they're added to the agent.
SECRET_FIELDS = frozenset({
    # Verified providers
    "LLM_API_KEY_ANTHROPIC",
    "LLM_API_KEY_OPENAI",
    "LLM_API_KEY_GEMINI",
    # Untested LiteLLM providers exposed in the UI dropdown
    "LLM_API_KEY_OPENROUTER",
    "LLM_API_KEY_GROQ",
    "LLM_API_KEY_MISTRAL",
    "LLM_API_KEY_DEEPSEEK",
    "LLM_API_KEY_TOGETHER_AI",
    "LLM_API_KEY_PERPLEXITY",
    "LLM_API_KEY_XAI",
    # Web search backends
    "BRAVE_API_KEY",
    "TAVILY_API_KEY",
})


# Non-secret env vars exposed by the Settings UI. Used in Docker
# mode to filter ``os.environ`` for display — the .env file isn't
# mounted inside the container, so values come from Compose's
# ``env_file:`` injection rather than from disk. Keep this in sync
# with the per-tab ``FIELDS`` arrays in
# ``web-client/src/components/Settings/*Tab.tsx``.
MANAGED_ENV_KEYS = frozenset({
    # Models tab
    "LLM_MODEL",
    "LLM_MODEL_ARBITER",
    "LLM_MODEL_DIAGNOSTIC",
    "LLM_MODEL_ANALYSER",
    "ENABLE_INTERRUPT_ARBITER",
    "ENABLE_DIAGNOSTIC_AGENT",
    "ENABLE_PASSIVE_ANALYSER",
    # Roon tab
    "ROON_CORE_URL",
    "ROON_CORE_NAME",
    "ROON_PROFILE_NAME",
    # Web search tab (provider keys live in SECRET_FIELDS)
    "WEB_SEARCH_PROVIDER",
    "SEARXNG_URL",
    # Speech tab
    "TTS_URL",
    # Analyser tab
    "ANALYSER_INTERVAL_MINUTES",
    "ANALYSER_STALENESS_MINUTES",
    "ANALYSER_BATCH_SIZE",
    # Persona tab
    "LLM_PERSONA",
})


def config_pristine() -> bool:
    """True while the user has set no assistant-configuration value.

    Considers only the variables the Settings pages manage
    (``MANAGED_ENV_KEYS`` + ``SECRET_FIELDS``) — operational overrides
    like the data dir, log file, or ports don't count. Drives the
    first-run Getting Started intro, which hides for good the moment
    anything is set, via the UI or a hand-edited ``.env``.
    """
    keys = MANAGED_ENV_KEYS | SECRET_FIELDS
    values = read_managed_env(keys)
    return not any((values.get(k) or "").strip() for k in keys)


_NOT_EDITABLE_REASON = (
    "To change values, edit `agent/.env` on the host, then click "
    "Restart below to apply and validate."
)


def _not_editable_error() -> str:
    """Plain-text version of the reason for use in error responses.
    The wire reason field is light-markdown (backticks mark inline
    code spans) so the banner can render the file path and command
    as monospace; error toasts render as plain text and would show
    the backticks literally without this strip."""
    return _NOT_EDITABLE_REASON.replace("`", "")


def handle_read(_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the current .env contents + metadata.

    Secret values are returned in plaintext over the same-origin WS;
    the UI masks them in the input display via ``secret_fields``.

    ``defaults`` is sourced from ``settings.BOOL_ENV_DEFAULTS`` —
    the single source of truth for bool env-var defaults. The UI
    uses these so toggle widgets render the effective value when
    the env var isn't explicitly set.

    ``editable`` flags whether the agent can write back to the .env
    file. False in Docker (no mount); the UI uses this to disable
    inputs and surface the banner explaining the host-edit workflow.
    """
    from app.settings import BOOL_ENV_DEFAULTS
    editable = env_editable()
    values = read_managed_env(MANAGED_ENV_KEYS | SECRET_FIELDS)
    defaults = {k: ("true" if v else "false") for k, v in BOOL_ENV_DEFAULTS.items()}
    return {
        "ok": True,
        "env_path": resolve_env_path_for_display(),
        "values": values,
        "defaults": defaults,
        "secret_fields": sorted(SECRET_FIELDS),
        "config_missing": required_config_missing(),
        "editable": editable,
        "editing_disabled_reason": None if editable else _NOT_EDITABLE_REASON,
    }


def handle_save(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply ``updates`` to the .env file.

    Empty / None values remove the key. Returns the new
    ``config_missing`` so the caller can decide whether to transition
    to chat. The optional ``restart`` flag is handled by the dispatch
    site, not here.

    Empty ``updates`` short-circuits to ok:true without touching disk
    — the Restart button uses this shape, and bypassing the write path
    keeps the .env untouched on Docker's read-only rootfs. Non-empty
    updates are rejected in Docker (.env isn't mounted).
    """
    updates = payload.get("updates")
    if not isinstance(updates, dict):
        return {
            "ok": False,
            "error": "Request must contain an 'updates' object",
        }

    invalid_keys = [k for k in updates if not _valid_env_key(k)]
    if invalid_keys:
        return {
            "ok": False,
            "error": "Invalid env-var name(s)",
            "invalid_keys": invalid_keys,
        }

    if not updates:
        return {
            "ok": True,
            "env_path": resolve_env_path_for_display(),
            "config_missing": required_config_missing(),
            "updated_keys": [],
        }

    if not env_editable():
        return {"ok": False, "error": _not_editable_error()}

    try:
        env_path = write_env_file(updates)
    except OSError as exc:
        log.exception("Failed to write .env")
        return {"ok": False, "error": f"Write failed: {exc}"}

    # Earlier-cached Settings references stay stale until restart.
    reload_env_into_process()
    reset_settings_for_tests()

    return {
        "ok": True,
        "env_path": str(env_path),
        "config_missing": required_config_missing(),
        "updated_keys": sorted(updates.keys()),
    }


def handle_reload(_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Re-read the .env file into ``os.environ``. Same cache caveat as
    ``handle_save``.

    Docker: rejected — ``os.environ`` was populated from Compose's
    ``env_file:`` directive at container start and can't be refreshed
    from inside; the container itself has to be restarted.
    """
    if not env_editable():
        return {"ok": False, "error": _not_editable_error()}

    env_path = reload_env_into_process()
    reset_settings_for_tests()
    return {
        "ok": True,
        "env_path": str(env_path),
        "config_missing": required_config_missing(),
    }


# ── Helpers ────────────────────────────────────────────────────────


_ENV_KEY_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def _valid_env_key(key: Any) -> bool:
    """Validator for env-var names — ASCII shape ``[A-Za-z_][A-Za-z0-9_]*``.
    Rejects unicode and special chars so the UI can't write garbage
    keys to the .env file."""
    if not isinstance(key, str):
        return False
    return _ENV_KEY_RE.match(key) is not None
