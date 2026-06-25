"""Locked-at-startup runtime configuration.

Single source of truth for every named environment variable Swarpius
reads. ``get_settings()`` builds the cached instance the first time it
is called and returns the same object forever after — guaranteeing that
the prompt builder, tool registry, request flow, and any other component
constructed at runtime can never disagree about a configuration value's
state. Env changes after the cache is populated take effect only on
process restart, the same as `.env`/`.env.test` semantics already
implied for cached state like the system prompt.

Tests that mutate ``os.environ`` should call
``reset_settings_for_tests()`` to invalidate the cache. The autouse
fixture in ``tests/conftest.py`` does this between every test by
default, so most tests "just work".

Dynamic-name env reads (skill ``requires_env`` frontmatter,
``LLM_API_KEY_<PROVIDER>`` where provider is discovered at runtime)
are kept as direct ``os.environ`` access at the call site — the
locking guarantee here covers every statically-named variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

# ── Env-var defaults ───────────────────────────────────────────────


# Single source of truth for bool env-var defaults. Read by
# ``_bool_env`` here and exposed via the /settings read endpoint so
# the UI's toggle widgets render the effective value when the env
# var isn't explicitly set.
BOOL_ENV_DEFAULTS: Dict[str, bool] = {
    # Optional sub-agents default off — increased cost is opt-in.
    # The Models tab in the UI exposes each toggle next to its
    # provider/model/key row.
    "ENABLE_DIAGNOSTIC_AGENT": False,
    "ENABLE_INTERRUPT_ARBITER": False,
    "ENABLE_PASSIVE_ANALYSER": False,
    # Caching is correct for every Anthropic user — on by default.
    "ENABLE_PROMPT_CACHING": True,
    "PARALLEL_TOOLS": False,
    "DISABLE_SIMULATED_STOP": False,
    "ENABLE_ROON_EXPLORER": False,
}


# ── Parsing helpers ────────────────────────────────────────────────


def _bool_env(name: str) -> bool:
    default = BOOL_ENV_DEFAULTS[name]
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _str_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _opt_str_env(name: str) -> Optional[str]:
    val = _str_env(name)
    return val or None


def _snapshot_api_keys() -> Dict[str, str]:
    """Capture every LLM_API_KEY_<PROVIDER> entry present in env at load
    time. The provider name is dynamic (discovered from the LLM_MODEL
    prefix), so we snapshot the whole namespace rather than enumerate."""
    return {
        k.removeprefix("LLM_API_KEY_"): v.strip()
        for k, v in os.environ.items()
        if k.startswith("LLM_API_KEY_") and v.strip()
    }


# ── Settings dataclass ────────────────────────────────────────────


@dataclass(frozen=True)
class Settings:
    # Feature flags
    enable_diagnostic_agent: bool
    enable_interrupt_arbiter: bool
    enable_prompt_caching: bool
    parallel_tools: bool
    enable_passive_analyser: bool
    enable_roon_explorer: bool

    # Tunables
    roon_max_parallel: int  # raw value; semantics via roon_max_parallel_batch
    roon_search_retry_limit: int
    roon_search_retry_delay: float
    log_retention_days: int
    chat_history_retention_days: int
    diagnostics_retention_days: int
    listening_history_retention_days: int
    conversation_history_max_turns: int
    conversation_idle_timeout_seconds: int
    llm_timeout_seconds: int
    search_history_max_entries: int
    execution_trace_max_length: int
    result_store_max_entries: int
    image_cache_max_entries: int
    analysis_history_max_entries: int
    analyser_batch_size: int
    analyser_interval_minutes: int
    analyser_staleness_minutes: int

    # Roon library marker for the `stop` action (transport).
    stop_marker_title: str
    disable_simulated_stop: bool

    # Identity / connection
    llm_persona: Optional[str]
    roon_core_url: Optional[str]
    roon_core_name: Optional[str]
    roon_profile_name: Optional[str]
    searxng_url: Optional[str]
    web_search_provider: Optional[str]
    brave_api_key: Optional[str]
    tavily_api_key: Optional[str]
    tts_url: Optional[str]
    swarpius_data_dir: Optional[str]
    log_file: Optional[str]
    # IANA timezone for all timestamps; None = system local (correct for
    # source/installer). Set when the process clock isn't local, e.g. Docker (UTC).
    time_zone: Optional[str]

    # WebSocket bind (--ws mode only). Defaults to loopback so source
    # and bundled-app installs aren't LAN-reachable unless the operator
    # sets SWARPIUS_WS_HOST=0.0.0.0. Docker sets that explicitly (the
    # container must listen broadly for port-publish), with host exposure
    # set by ${SWARPIUS_BIND_IP} in the compose port mapping (defaults
    # to 127.0.0.1). See SECURITY.md.
    ws_host: str
    ws_port: int

    # Model selection (provider/model strings). ``llm_model`` is the
    # coordinator's model and the fallback for the three optional
    # sub-agents; the ``_arbiter`` / ``_diagnostic`` / ``_analyser``
    # overrides are all optional.
    llm_model: Optional[str]
    llm_model_arbiter: Optional[str]
    llm_model_diagnostic: Optional[str]
    llm_model_analyser: Optional[str]

    # Per-provider API keys, snapshotted at load time. Lookup via
    # ``api_key_for_provider(provider)``.
    api_keys: Dict[str, str] = field(default_factory=dict)

    # ── Derived helpers ───────────────────────────────────────

    @property
    def roon_max_parallel_batch(self) -> Optional[int]:
        """Batch size for parallel-safe Roon calls. Positive int → use
        it; values < 1 → unlimited (None). Default 5 keeps Roon Cores
        from dropping or stalling responses on large multi-track
        requests."""
        return self.roon_max_parallel if self.roon_max_parallel >= 1 else None

    def model_for(self, agent: str) -> Optional[str]:
        """Per-agent override; falls back to the base ``llm_model``.
        The coordinator always uses ``llm_model`` directly."""
        agent = agent.lower()
        if agent == "coordinator":
            return self.llm_model
        if agent == "arbiter":
            return self.llm_model_arbiter or self.llm_model
        if agent == "diagnostic":
            return self.llm_model_diagnostic or self.llm_model
        if agent == "analyser":
            return self.llm_model_analyser or self.llm_model
        raise ValueError(f"Unknown agent: {agent!r}")

    def agent_enabled(self, agent: str) -> bool:
        """Coordinator is always on; the three optional sub-agents are
        gated by their ENABLE flag."""
        agent = agent.lower()
        if agent == "coordinator":
            return True
        if agent == "arbiter":
            return self.enable_interrupt_arbiter
        if agent == "diagnostic":
            return self.enable_diagnostic_agent
        if agent == "analyser":
            return self.enable_passive_analyser
        raise ValueError(f"Unknown agent: {agent!r}")

    def api_key_for_provider(self, provider: str) -> Optional[str]:
        return self.api_keys.get(provider.upper()) or None

    # ── Loader ─────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            enable_diagnostic_agent=_bool_env("ENABLE_DIAGNOSTIC_AGENT"),
            enable_interrupt_arbiter=_bool_env("ENABLE_INTERRUPT_ARBITER"),
            enable_prompt_caching=_bool_env("ENABLE_PROMPT_CACHING"),
            parallel_tools=_bool_env("PARALLEL_TOOLS"),
            enable_passive_analyser=_bool_env("ENABLE_PASSIVE_ANALYSER"),
            enable_roon_explorer=_bool_env("ENABLE_ROON_EXPLORER"),
            roon_max_parallel=_int_env("ROON_MAX_PARALLEL", 5),
            roon_search_retry_limit=_int_env("ROON_SEARCH_RETRY_LIMIT", 2),
            roon_search_retry_delay=_float_env("ROON_SEARCH_RETRY_DELAY", 1.0),
            log_retention_days=max(1, _int_env("LOG_RETENTION_DAYS", 7)),
            chat_history_retention_days=_int_env("CHAT_HISTORY_RETENTION_DAYS", 90),
            diagnostics_retention_days=_int_env("DIAGNOSTICS_RETENTION_DAYS", 30),
            listening_history_retention_days=_int_env("LISTENING_HISTORY_RETENTION_DAYS", 365),
            conversation_history_max_turns=_int_env("CONVERSATION_HISTORY_MAX_TURNS", 5),
            conversation_idle_timeout_seconds=_int_env("CONVERSATION_IDLE_TIMEOUT_SECONDS", 300),
            llm_timeout_seconds=max(1, _int_env("LLM_TIMEOUT_SECONDS", 60)),
            search_history_max_entries=_int_env("SEARCH_HISTORY_MAX_ENTRIES", 5),
            execution_trace_max_length=_int_env("EXECUTION_TRACE_MAX_LENGTH", 10),
            result_store_max_entries=_int_env("RESULT_STORE_MAX_ENTRIES", 50),
            image_cache_max_entries=_int_env("IMAGE_CACHE_MAX_ENTRIES", 200),
            analysis_history_max_entries=_int_env("ANALYSIS_HISTORY_MAX_ENTRIES", 20),
            analyser_batch_size=_int_env("ANALYSER_BATCH_SIZE", 5),
            analyser_interval_minutes=_int_env("ANALYSER_INTERVAL_MINUTES", 30),
            analyser_staleness_minutes=_int_env("ANALYSER_STALENESS_MINUTES", 60),
            stop_marker_title=_str_env(
                "ROON_STOP_MARKER_TITLE", "Swarpius_Stop_Playback",
            ),
            disable_simulated_stop=_bool_env("DISABLE_SIMULATED_STOP"),
            llm_persona=_opt_str_env("LLM_PERSONA"),
            roon_core_url=_opt_str_env("ROON_CORE_URL"),
            roon_core_name=_opt_str_env("ROON_CORE_NAME"),
            roon_profile_name=_opt_str_env("ROON_PROFILE_NAME"),
            searxng_url=_opt_str_env("SEARXNG_URL"),
            web_search_provider=_opt_str_env("WEB_SEARCH_PROVIDER"),
            brave_api_key=_opt_str_env("BRAVE_API_KEY"),
            tavily_api_key=_opt_str_env("TAVILY_API_KEY"),
            tts_url=_opt_str_env("TTS_URL"),
            swarpius_data_dir=_opt_str_env("SWARPIUS_DATA_DIR"),
            log_file=_opt_str_env("LOG_FILE"),
            time_zone=_opt_str_env("SWARPIUS_TIMEZONE"),
            ws_host=_opt_str_env("SWARPIUS_WS_HOST") or "127.0.0.1",
            ws_port=_int_env("SWARPIUS_WS_PORT", 8080),
            llm_model=_opt_str_env("LLM_MODEL"),
            llm_model_arbiter=_opt_str_env("LLM_MODEL_ARBITER"),
            llm_model_diagnostic=_opt_str_env("LLM_MODEL_DIAGNOSTIC"),
            llm_model_analyser=_opt_str_env("LLM_MODEL_ANALYSER"),
            api_keys=_snapshot_api_keys(),
        )


# ── Singleton access ──────────────────────────────────────────────


_cached: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance, loading on first
    call. All subsequent reads receive the same object — env mutations
    after first access have no effect until the cache is reset."""
    global _cached
    if _cached is None:
        _cached = Settings.from_env()
    return _cached


def reset_settings_for_tests() -> None:
    """Drop the cached settings so the next ``get_settings()`` call
    reads ``os.environ`` afresh. Used by the autouse pytest fixture in
    ``tests/conftest.py`` to give each test a clean slate."""
    global _cached
    _cached = None


# ── Required-config detection ──────────────────────────────────────


# Providers that don't require an API key (running locally).
_LOCAL_PROVIDERS = frozenset({"ollama", "ollama_chat"})


def required_config_missing(settings: Optional[Settings] = None) -> list[str]:
    """Return the env var names that are required-but-unset.

    The Settings UI routes the user here when this list is non-empty,
    with a clear banner showing exactly which fields need attention.
    The startup banner / first-run console message also surface this.

    Currently the minimum to answer chat requests is:
    - A coordinator model (``LLM_MODEL``) with a ``provider/model``
      shape.
    - An API key for every distinct provider used across all enabled
      agents (coordinator + any of arbiter / diagnostic / analyser
      whose ``ENABLE_*`` toggle is on). Disabled agents are skipped
      since they never construct an LLM client.

    Returns an empty list when everything required is satisfied.
    """
    if settings is None:
        settings = get_settings()

    missing: list[str] = []

    coord_model = settings.model_for("coordinator")
    if not coord_model or "/" not in coord_model:
        missing.append("LLM_MODEL")
        return missing
    if not coord_model.split("/", 1)[0]:
        missing.append("LLM_MODEL")
        return missing

    seen: set[str] = set()
    for agent in ("coordinator", "arbiter", "diagnostic", "analyser"):
        if not settings.agent_enabled(agent):
            continue
        model = settings.model_for(agent)
        if not model or "/" not in model:
            continue
        provider = model.split("/", 1)[0].strip()
        if not provider or provider.lower() in _LOCAL_PROVIDERS:
            continue
        upper = provider.upper()
        if upper in seen:
            continue
        seen.add(upper)
        if not settings.api_key_for_provider(provider):
            missing.append(f"LLM_API_KEY_{upper}")

    return missing


def required_config_complete(settings: Optional[Settings] = None) -> bool:
    """Convenience: True when no required config is missing."""
    return len(required_config_missing(settings)) == 0
