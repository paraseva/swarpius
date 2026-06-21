"""Initialisation + per-event-listener methods on ``RuntimeState``.

Split out of ``state.py`` to keep the main file focused on the class
declaration and ``__init__``. Composed via inheritance — methods are
called as ``runtime.ensure_initialised()`` exactly as before.

The ``_setup_*`` phase helpers each own one phase of the init
choreography (LLM clients, Roon connection, tool registration,
skills + prompt). Touching one phase means opening this file, not
scrolling past the others.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from app.constants import (
    CHANNEL_QUEUE_UPDATES,
    CHANNEL_ZONE_SNAPSHOTS,
)
from app.coordinator.context_providers import ConversationHistoryProvider
from app.data_paths import AGENT_ROOT, data_dir
from app.llm.tool_registry import ToolRegistry
from app.roon.stop_marker import StopMarkerCoordinator
from app.roon.zone_artwork_service import ZoneArtworkCache
from app.runtime.url_parse import parse_host_port as _parse_host_port

# Patched-in-tests symbols are referenced via the state module so tests
# patching ``app.runtime.state.RoonConnection`` (and friends) still
# reach the init flow even though the methods now live here. Imported
# lazily inside the methods that need them to avoid the circular load
# (state.py imports this mixin at top-level).

_log = logging.getLogger("swarpius.runtime")


class _StateInitMixin:
    """Init choreography and the Roon live-event listener.

    The mixin assumes the cross-cutting instance attributes set up in
    ``RuntimeState.__init__`` (e.g. ``self.zone_domain``,
    ``self.tool_registry``, ``self.result_store``, the providers).
    """

    def _reset_partial_init(self) -> None:
        """Clear state left behind by a failed prior ensure_initialised().

        If a previous attempt raised mid-init, references to LLM clients,
        the Roon connection, and (crucially) partially-populated tool
        registrations sit on self. Running init fresh over that partial
        state would log duplicate-registration warnings and leave the
        old Roon connection reachable. This resets everything so the
        retry starts from the same state as a first-time call.
        """
        # Detect "there's partial state" via the Roon connection slot
        # (nothing above it in the init sequence leaves state requiring
        # cleanup, since model-profile / LLMClient assignments are just
        # overwrites).
        if self.roon_connection is None and len(self.tool_registry) == 0:
            return
        self.llm_clients.reset()
        self.agent_skills = []
        self.roon_connection = None
        self.tool_registry = ToolRegistry()
        # Context providers: clear any content they accumulated so the
        # retry's set_context calls start from empty.
        self.skills_provider.set_context("")
        self.key_rules_provider.set_context("")

    def _forward_roon_live_event(self, event_payload: Dict[str, Any]) -> None:
        """Translate a Roon live event into WebSocket emissions.

        CLI mode short-circuits before any emission.
        """
        if self._run_mode_getter() != "ws":
            return

        event_type = event_payload.get("type")

        if event_type == "state":
            # Promote paused→playing on a seek tick: after group/
            # ungroup Roon transitions paused→playing without firing
            # zones_changed. Don't touch stopped — track-end fires a
            # lingering seek event that would otherwise revert the stop.
            if event_payload.get("event") == "zones_seek_changed" and self.roon_connection:
                for zone_id in event_payload.get("changed_ids", []):
                    zone = self.roon_connection.api.zones.get(zone_id)
                    if zone is not None and zone.get("state") == "paused":
                        zone["state"] = "playing"
            self._emit_zone_snapshot_if_changed()
            self._reconcile_zone_state()
            return

        if event_type == "queue":
            zone_id = event_payload.get("zone_id")
            zone = self.roon_connection.api.zones.get(zone_id, {}) if self.roon_connection else {}
            items = event_payload.get("data", {}).get("items", [])
            self._ws_send_callback(CHANNEL_QUEUE_UPDATES, {
                "zone_id": zone_id,
                "zone_display_name": zone.get("display_name", ""),
                "items": items,
            })
            # A queue event on a "stopped" zone signals api.zones has
            # diverged from Roon's view (transient state from a topology
            # change). Refresh via /get_zones on the asyncio executor —
            # a synchronous call here would deadlock against Roon's WS
            # receive thread.
            if zone.get("state") == "stopped" and self.roon_connection:
                self._spawn_zone_refresh()
            else:
                self._emit_zone_snapshot_if_changed()

    def _spawn_zone_refresh(self) -> None:
        loop = self._get_ws_event_loop()
        if loop is None:
            return
        loop.call_soon_threadsafe(
            loop.run_in_executor, None, self._refresh_zones_and_emit_snapshot,
        )

    def _refresh_zones_and_emit_snapshot(self) -> None:
        if self.roon_connection:
            self.roon_connection.refresh_zones_from_api()
        self._emit_zone_snapshot_if_changed()

    def _emit_zone_snapshot_if_changed(self) -> None:
        if not self.roon_connection:
            return
        snapshot = self.zone_snapshot.build(self.roon_connection.api.zones)
        if not self.zone_snapshot.changed_since_last(snapshot):
            return
        self._ws_send_callback(CHANNEL_ZONE_SNAPSHOTS, {
            "source": "[Roon snapshot]",
            "data": {
                "zones": snapshot,
                "timestamp_ms": int(time.time() * 1000),
            },
        })

    def ensure_initialised(self) -> None:
        if self.initialised:
            return

        with self._init_lock:
            # Re-check under the lock: a concurrent caller may have
            # completed initialisation while we waited.
            if self.initialised:
                return
            self._ensure_initialised_locked()

    def _ensure_initialised_locked(self) -> None:
        # If a previous attempt raised partway, clear its residual state
        # before starting fresh so tool re-registration is clean.
        self._reset_partial_init()

        from app.settings import get_settings
        settings = get_settings()

        self._apply_settings_capacity_overrides(settings)
        coord_model = self._setup_llm_clients()
        self._setup_roon_connection(settings)
        web_search_tool = self._setup_tool_registry(settings)
        self._setup_skills_and_prompt()
        self.initialised = True
        self._log_startup_summary(coord_model, settings, web_search_tool)

    def _apply_settings_capacity_overrides(self, settings) -> None:
        """Rebuild objects whose construction-time caps depend on
        settings. ``__init__`` constructed them with the same defaults
        Settings uses when the env var is unset, so this does nothing
        when the user hasn't overridden them."""
        if settings.conversation_history_max_turns != 5:
            self.conversation_history_provider = ConversationHistoryProvider(
                "Conversation History",
                max_turns=settings.conversation_history_max_turns,
            )
        if settings.image_cache_max_entries != 200:
            self.zones.replace_artwork(ZoneArtworkCache(
                max_entries=settings.image_cache_max_entries,
            ))

    def _setup_llm_clients(self) -> str:
        """Resolve per-agent model specs and delegate to
        :class:`LLMClientsManager` for construction. Returns the
        resolved coordinator model name for the startup-summary line."""
        from app.runtime import state as _state
        from app.runtime.llm_clients import format_profile_log_line

        default_model = self._resolve_agent_model("LLM_MODEL")
        arbiter_spec = self._resolve_agent_model("LLM_MODEL_ARBITER", default=default_model)
        diagnostic_spec = self._resolve_agent_model("LLM_MODEL_DIAGNOSTIC", default=default_model)
        yaml_config = _state.load_yaml_profiles_with_override(
            AGENT_ROOT / "model_profiles.yaml",
            data_dir() / "model_profiles.yaml",
        )

        return self.llm_clients.build(
            default_model=default_model,
            arbiter_spec=arbiter_spec,
            diagnostic_spec=diagnostic_spec,
            parse_model_spec=self._parse_model_spec,
            get_model_profile=_state.get_model_profile,
            yaml_config=yaml_config,
            log_callback=lambda resolved: _log.info("%s", format_profile_log_line(resolved)),
        )

    def _setup_roon_connection(self, settings) -> None:
        """Connect to the Roon Core, hydrate zone state, and warm the
        stop-marker coordinator."""
        # ``RoonConnection`` resolved via the state module so test
        # patches at ``app.runtime.state.RoonConnection`` reach here.
        from app.runtime import state as _state

        roon_core_url = settings.roon_core_url or ""
        roon_core_host = None
        roon_core_port = None
        if roon_core_url:
            roon_core_host, roon_core_port = _parse_host_port(
                roon_core_url,
                default_host="localhost",
                default_port=9100,
            )

        roon_connection = _state.RoonConnection(
            default_zone=settings.default_roon_zone,
            roon_core_host=roon_core_host,
            roon_core_port=roon_core_port,
            profile=settings.roon_profile_name,
            lifecycle_callback=self.roon_lifecycle_callback,
        )
        self.roon_connection = roon_connection
        # Restore Roon-scoped persisted state (browse-session ref pool, queue
        # references) now that the connection — and its session manager —
        # exist. No-op when persistence is not wired (e.g. tests).
        if self._persistence_manager is not None:
            self.attach_roon_persistence(self._persistence_manager)
        self._load_zone_aliases()
        self._zone_cache = self._build_zone_cache()

        roon_connection.register_event_listener(self._forward_roon_live_event)
        roon_connection.register_event_listener(self.play_history.handle_event)
        self.play_history.set_stop_marker_title(settings.stop_marker_title)

        # Stop-marker coordinator: builds + warms cache once Roon is
        # alive. initialise() walks marker → action_list and stores the
        # post-wrapper track_item_key, so the first user-issued stop
        # already costs only the two-call dispatch (not search+drill).
        # Disabled mode short-circuits before any Roon calls.
        self.stop_marker_coordinator = StopMarkerCoordinator(
            connection=roon_connection,
            marker_title=settings.stop_marker_title,
            broadcast_state=self._broadcast_feature_availability,
            disabled=settings.disable_simulated_stop,
        )
        self.stop_marker_coordinator.initialise()

    def _setup_tool_registry(self, settings):
        """Delegate to :func:`register_runtime_tools`."""
        from app.runtime.tool_bootstrap import register_runtime_tools
        return register_runtime_tools(self, settings)

    def _setup_skills_and_prompt(self) -> None:
        """Load skill docs, filter against the registered tool set, and
        assemble the coordinator system prompt.

        Skills declaring ``requires_tool: <name>`` only survive if the
        named tool actually got registered — env-var presence alone is
        not enough, since the factory may decline to build a tool even
        when its credentials are set (WEB_SEARCH_PROVIDER mismatch,
        unknown value, explicit ``none``). Critical directives are
        extracted into the key-rules provider so they can be placed in
        the highest-attention position.
        """
        # Skill loaders resolved via the state module so test patches at
        # ``app.runtime.state._load_agent_skills`` /
        # ``_format_agent_skills_for_prompt`` reach here.
        from app.runtime import state as _state

        loaded_skills = _state._load_agent_skills(self.skills_dir)
        kept_skills = _state._filter_skills_by_registered_tools(
            loaded_skills, set(self.tool_registry.tool_names),
        )
        self.agent_skills = kept_skills
        skills_block, key_rules = _state._format_agent_skills_for_prompt(kept_skills)
        self.skills_provider.set_context(skills_block)
        self.key_rules_provider.set_context(key_rules)
        self.coordinator_system_prompt = self.build_coordinator_system_prompt()

    def _log_startup_summary(self, coord_model: str, settings, web_search_tool) -> None:
        from app.coordinator.request_flow import is_prompt_caching_enabled
        from app.llm.diagnostic_agent import is_diagnostic_agent_enabled
        _log.info(
            "Swarpius initialised — coordinator=%s  zone=%s  profile=%s  "
            "prompt_caching=%s  diagnostic_agent=%s  web_search=%s",
            coord_model,
            settings.default_roon_zone or "",
            settings.roon_profile_name or "(default)",
            is_prompt_caching_enabled(),
            is_diagnostic_agent_enabled(),
            web_search_tool.provider_name if web_search_tool else "disabled",
        )
