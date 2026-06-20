from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from app.coordinator.context_providers import (
    CallbackContextProvider,
    ConversationHistoryProvider,
    CurrentDateProvider,
    CurrentTimeProvider,
    TextContextProvider,
)
from app.coordinator.skill_docs import AgentSkillDocument
from app.coordinator.skill_loader import (  # noqa: F401 — patched by tests via app.runtime.state.*
    filter_skills_by_registered_tools as _filter_skills_by_registered_tools,
)
from app.coordinator.skill_loader import (  # noqa: F401 — patched by tests via app.runtime.state.*
    format_agent_skills_for_prompt as _format_agent_skills_for_prompt,
)
from app.coordinator.skill_loader import (  # noqa: F401 — patched by tests via app.runtime.state.*
    load_agent_skills as _load_agent_skills,
)
from app.data_paths import (
    AGENT_ROOT,
    _running_from_bundle,
    config_dir,
    play_history_path,
)
from app.llm.client import LLMClient
from app.llm.model_profiles import (  # noqa: F401 — get_model_profile / load_yaml_profiles patched by tests via app.runtime.state.*
    ModelProfile,
    ResolvedProfile,
    get_model_profile,
    load_yaml_profiles,
    load_yaml_profiles_with_override,
)
from app.llm.tool_registry import ToolRegistry
from app.roon.config_action_service import ConfigActionService
from app.roon.control_service import RoonControlService
from app.roon.play_history import PlayHistoryStore
from app.roon.stop_marker import StopMarkerCoordinator
from app.roon.zone_artwork_service import ZoneArtworkCache
from app.roon.zone_domain import ZoneDomain
from app.roon.zone_snapshot import ZoneSnapshotBuilder
from app.runtime.llm_clients import LLMClientsManager
from app.runtime.result_store_manager import ResultStoreManager
from app.runtime.result_store_types import ResultStoreEntry
from app.runtime.state_helpers import (
    _backend_ok,
    _build_web_search_tool,  # noqa: F401 — imported by tests via app.runtime.state.*
)
from app.runtime.state_init_mixin import _StateInitMixin
from app.runtime.state_internals import (
    SearchHistoryEntry,
    _BoundedDict,  # re-exported for tests that `from app.runtime.state import _BoundedDict`  # noqa: F401
    _locks_result_store,
    _locks_zone_state,
)
from app.runtime.state_zone_mixin import _StateZoneMixin
from app.runtime.working_memory_persistence import WorkingMemoryState
from app.runtime.zones import ZoneSubsystem

if TYPE_CHECKING:
    from app.runtime.persistence import PersistenceManager, PersistentState
from roon_core.connection import RoonConnection
from usage_metrics import UsageTracker

# Public surface — includes intentional re-exports of skill_loader /
# model_profiles / state_helpers / state_internals symbols that tests
# patch via ``app.runtime.state.<name>``. Declaring them in ``__all__``
# documents the contract at the module level and stops static analysers
# (CodeQL "unused import", etc.) from flagging the re-export imports.
__all__ = [
    "RuntimeState",
    "_filter_skills_by_registered_tools",
    "_format_agent_skills_for_prompt",
    "_load_agent_skills",
    "get_model_profile",
    "load_yaml_profiles",
    "load_yaml_profiles_with_override",
    "_build_web_search_tool",
    "_BoundedDict",
]

_SOURCE_LABELS = {"roon_search": "Roon", "web_search": "Web"}


class RuntimeState(_StateInitMixin, _StateZoneMixin):
    """Central per-process runtime state for the agent.

    Composed from themed mixins so each concern lives in its own file:
      - ``_StateInitMixin`` (state_init_mixin.py) — ensure_initialised,
        the _setup_* phase helpers, _reset_partial_init, the Roon
        live-event listener.
      - ``_StateZoneMixin`` (state_zone_mixin.py) — zone aliases,
        cache, resolution, broadcasts.

    What stays here: __init__ (object construction), result-store /
    search-history helpers, Roon control + artwork + feature-availability
    methods, model-spec parsing, prompt builders, and the public
    ``get_context_sections`` aggregator.
    """

    def __init__(self) -> None:
        # LLM-client state lives on the manager; legacy attributes
        # (runtime.llm_client / .arbiter_client / .diagnostic_client
        # plus the four resolved profiles) become property
        # delegations below.
        self.llm_clients = LLMClientsManager()
        self.tool_registry = ToolRegistry()
        self.roon_connection: Optional[RoonConnection] = None
        self.coordinator_system_prompt: str = ""
        self.execution_trace_provider = TextContextProvider("Execution Trace")
        self.skills_provider = TextContextProvider("Skill Definitions")
        self.key_rules_provider = TextContextProvider("Key Rules")
        self.search_history_provider = TextContextProvider("Search History")
        # ConversationHistoryProvider's deque maxlen is fixed at
        # construction. Use its built-in default here; ensure_initialised
        # rebuilds with the locked settings value once env is finalised.
        self.conversation_history_provider = ConversationHistoryProvider(
            "Conversation History",
        )
        self.current_date_provider = CurrentDateProvider("Current Date")
        self.current_time_provider = CurrentTimeProvider("Current Time")
        self.zone_aliases_provider = CallbackContextProvider(
            "Zone Aliases", self._get_zone_aliases_context,
        )
        self.zone_status_provider = CallbackContextProvider(
            "Zone Status", self._get_zone_status_context,
        )
        self.results = ResultStoreManager()
        # Shared by-reference views into the result store. Tools capture
        # these dicts/lists at construction time, so the runtime exposes
        # the same underlying objects rather than copies. NEVER reassign;
        # always mutate in-place (or via the manager methods).
        self.result_store: Dict[str, Any] = self.results.entries
        self.search_history: List[SearchHistoryEntry] = self.results.history
        self.queue_display_cache: Dict[str, str] = {}
        self.execution_trace: List[Dict[str, Any]] = []
        self.global_step: int = 0
        self.llm_call_count: int = 0
        self.validation_retry_count: int = 0
        self.skills_dir = AGENT_ROOT / "skills"
        # ``stop_marker_title`` is wired in ``_ensure_initialised_locked``
        # via ``set_stop_marker_title`` so ``__init__`` does not snapshot
        # Settings — that would cache an empty ``LLM_MODEL`` on CI and
        # shadow the env patches tests apply between construction and
        # ``ensure_initialised``.
        self.play_history = PlayHistoryStore(store_path=play_history_path())
        self.play_history.load()
        # Zone subsystem bundles the two existing state owners and
        # exposes the artwork-cache mirror handles tests rely on.
        # Property delegations below preserve the legacy runtime.<attr>
        # API. Initial artwork cap matches Settings'
        # image_cache_max_entries default (200);
        # ensure_initialised swaps in the locked value if overridden.
        self.zones = ZoneSubsystem(
            domain=ZoneDomain(
                zone_aliases_path=config_dir() / "zone_aliases.json",
                get_roon_connection=lambda: self.roon_connection,
                ws_send=lambda c, p: self._ws_send_callback(c, p),
                get_last_played_dict=self.play_history.get_last_played_dict,
            ),
            artwork=ZoneArtworkCache(max_entries=200),
        )
        self.zone_snapshot = ZoneSnapshotBuilder(
            get_alias=self._get_alias_for_zone,
        )
        # Guards result_store + search_history. Under PARALLEL_TOOLS,
        # concurrent tool executions call store_result_entries; without
        # serialisation, the append+evict sequence races. RLock because
        # store_result_entries calls store_result_handle.
        self.result_store_lock = self.results.lock
        self.usage_tracker = UsageTracker(window_seconds=60)
        self.roon_control = RoonControlService(
            roon_connection_getter=lambda: self.roon_connection,
            resolve_zone_name=self.resolve_zone_name,
            get_alias_for_zone=self._get_alias_for_zone,
            broadcast_default_zone=self._broadcast_default_zone,
            stop_marker_coordinator_getter=lambda: self.stop_marker_coordinator,
        )
        self.config_action = ConfigActionService(
            zone_domain=self.zone_domain,
            get_roon_connection=lambda: self.roon_connection,
            broadcast_zone_labels=self._broadcast_zone_labels,
        )
        self.rate_limit_override_event = threading.Event()
        self.arbiter_executor = ThreadPoolExecutor(max_workers=1)
        self.shutdown_event: Optional[threading.Event] = None
        # LLM clients + profiles → self.llm_clients (constructed at
        # the top of __init__); see the property delegations below.
        self.agent_skills: List[AgentSkillDocument] = []
        self.initialised = False
        # Serialises ``ensure_initialised()`` so the WS-mode background
        # init thread and the per-connection ``websocket_handler`` call
        # can't both run the Roon pairing flow at the same time. Without
        # this, concurrent callers build duplicate ``RoonConnection``
        # instances during the auth window, each firing its own
        # ``RoonApi.register()`` — the Core treats them as separate
        # extension requests, producing a pending-approval entry per
        # caller.
        self._init_lock = threading.Lock()
        # Stop-marker coordinator: owns the cached track_item_key + the
        # `available` flag for the silent-marker stop feature. Built
        # eagerly during initialise() once the Roon connection is alive,
        # so the first user-issued stop already has a hot cache. None
        # until then; never None after initialise() completes.
        self.stop_marker_coordinator: Optional[StopMarkerCoordinator] = None
        self._run_mode_getter: Callable[[], str] = lambda: "cli"
        self._ws_send_callback: Callable[[str, Any], None] = lambda _c, _b: None
        # ``None`` in CLI mode; in WS mode the loop is used to dispatch
        # blocking Roon API calls off Roon's WS receive thread.
        self._get_ws_event_loop: Callable[[], Optional[Any]] = lambda: None
        # Optional callback fired by RoonConnection during the
        # connect lifecycle (discovery → pairing → authorised →
        # connecting). CLI sets this to update its startup
        # spinner; WS leaves it None.
        self.roon_lifecycle_callback: Optional[Callable[[str], None]] = None

        # Roon setup state, surfaced to the frontend via
        # feature-availability. Values: "initialising" | "paired" |
        # "failed". Roon-related WS handlers (chat etc.) are gated
        # on this in the UI routing layer.
        self.roon_state: str = "initialising"
        self.roon_status_message: str = ""
        self.roon_failure_reason: Optional[str] = None

    # ── LLM-client property delegations ────────────────────────────
    # State lives on self.llm_clients; these preserve the legacy
    # runtime.<attr> API for existing callers (request_flow,
    # startup_banner, tests that do rs.llm_client = MagicMock()).

    @property
    def llm_client(self) -> Optional[LLMClient]:
        return self.llm_clients.coordinator

    @llm_client.setter
    def llm_client(self, value: Optional[LLMClient]) -> None:
        self.llm_clients.coordinator = value

    @property
    def arbiter_client(self) -> Optional[LLMClient]:
        return self.llm_clients.arbiter

    @arbiter_client.setter
    def arbiter_client(self, value: Optional[LLMClient]) -> None:
        self.llm_clients.arbiter = value

    @property
    def diagnostic_client(self) -> Optional[LLMClient]:
        return self.llm_clients.diagnostic

    @diagnostic_client.setter
    def diagnostic_client(self, value: Optional[LLMClient]) -> None:
        self.llm_clients.diagnostic = value

    @property
    def model_profile(self) -> Optional[ModelProfile]:
        return self.llm_clients.coordinator_model_profile

    @model_profile.setter
    def model_profile(self, value: Optional[ModelProfile]) -> None:
        self.llm_clients.coordinator_model_profile = value

    @property
    def resolved_profile(self) -> Optional[ResolvedProfile]:
        return self.llm_clients.coordinator_resolved

    @resolved_profile.setter
    def resolved_profile(self, value: Optional[ResolvedProfile]) -> None:
        self.llm_clients.coordinator_resolved = value

    @property
    def resolved_arbiter_profile(self) -> Optional[ResolvedProfile]:
        return self.llm_clients.arbiter_resolved

    @resolved_arbiter_profile.setter
    def resolved_arbiter_profile(self, value: Optional[ResolvedProfile]) -> None:
        self.llm_clients.arbiter_resolved = value

    @property
    def resolved_diagnostic_profile(self) -> Optional[ResolvedProfile]:
        return self.llm_clients.diagnostic_resolved

    @resolved_diagnostic_profile.setter
    def resolved_diagnostic_profile(self, value: Optional[ResolvedProfile]) -> None:
        self.llm_clients.diagnostic_resolved = value

    # ── Zone-subsystem property delegations ────────────────────────
    # State lives on self.zones (ZoneSubsystem bundling ZoneDomain +
    # ZoneArtworkCache); these preserve the legacy attribute API.

    @property
    def zone_domain(self) -> ZoneDomain:
        return self.zones.domain

    @zone_domain.setter
    def zone_domain(self, value: ZoneDomain) -> None:
        self.zones.domain = value

    @property
    def zone_artwork(self) -> ZoneArtworkCache:
        return self.zones.artwork

    @zone_artwork.setter
    def zone_artwork(self, value: ZoneArtworkCache) -> None:
        self.zones.replace_artwork(value)

    @property
    def zone_state_lock(self):
        return self.zones.state_lock

    @property
    def image_base64_cache(self):
        return self.zones.image_base64_cache

    @property
    def zone_artwork_lock(self):
        return self.zones.artwork_lock

    # ── Zone-state dict proxies ────────────────────────────────────
    # Read access returns the underlying mutable dict so captures
    # (e.g. tools reading zone_aliases) see in-place mutations. Write
    # access (``rs.zone_aliases = {...}``, used widely by tests to
    # inject state) replaces contents in place so identity is preserved.

    def set_prompt_state_context(self) -> None:
        """Inject a compact search history index into the coordinator context."""
        self.search_history_provider.set_context(self._render_search_history())

    @_locks_result_store
    def _render_search_history(self) -> str:
        if not self.search_history:
            return ""
        lines = [
            "Recent search results are cached below. "
            "Use result_fetch with the result_handle to retrieve items "
            "and references before acting on them. "
            "Never guess references if they are not in the execution trace — always retrieve them via result_fetch.",
        ]
        for entry in self.search_history:
            source = _SOURCE_LABELS.get(entry.tool_name, entry.tool_name)
            lines.append(
                f"[{entry.result_handle}] {entry.timestamp_display}"
                f" | {source}: {entry.description}"
                f" | {entry.item_count} items"
            )
        return "\n".join(lines)

    @_locks_result_store
    def _lookup_reference_title(self, handle: str, reference: str) -> Optional[str]:
        """Find the title of an item by reference in a cached result."""
        payload = self.result_store.get(handle)
        if not isinstance(payload, list):
            return None
        for group in payload:
            if isinstance(group, dict) and "items" in group:
                for item in group["items"]:
                    if isinstance(item, dict) and item.get("reference") == reference:
                        return item.get("title")
            elif isinstance(group, dict) and group.get("reference") == reference:
                return group.get("title")
        return None


    def configure_io_callbacks(
        self,
        run_mode_getter: Callable[[], str],
        ws_send_callback: Callable[[str, Any], None],
        get_ws_event_loop: Callable[[], Optional[Any]] = lambda: None,
    ) -> None:
        self._run_mode_getter = run_mode_getter
        self._ws_send_callback = ws_send_callback
        self._get_ws_event_loop = get_ws_event_loop

    def store_result_handle(self, payload: Any) -> str:
        return self.results.store_handle(payload)

    def store_result_entries(self, entries: List[ResultStoreEntry]) -> List[str]:
        return self.results.store_entries(entries)

    @_locks_zone_state
    def perform_config_action(
        self,
        action: str,
        zone: Optional[str] = None,
        zone_to_transfer_to: Optional[str] = None,
        alias: Optional[str] = None,
        group_zones: Optional[List[str]] = None,
        new_name: Optional[str] = None,
    ) -> str:
        return self.config_action.perform(
            action,
            zone=zone,
            zone_to_transfer_to=zone_to_transfer_to,
            alias=alias,
            group_zones=group_zones,
            new_name=new_name,
        )

    def get_initial_zone_snapshot(self) -> Dict[str, Any]:
        """Build a zone snapshot for a WS client that just connected.
        Includes every zone Roon knows about; the client renders what
        it gets."""
        zones: list[Dict[str, Any]] = []
        if self.roon_connection:
            zones = self.zone_snapshot.build(self.roon_connection.api.zones)
        return {
            "source": "[Roon snapshot]",
            "data": {
                "zones": zones,
                "timestamp_ms": int(time.time() * 1000),
            },
        }

    def get_initial_queue_events(self) -> list[Dict[str, Any]]:
        """Return current queue data for all zones with queue subscriptions."""
        if not self.roon_connection:
            return []
        events: list[Dict[str, Any]] = []
        for zone_id, queue_payload in self.roon_connection.last_queue_events_by_zone.items():
            zone = self.roon_connection.api.zones.get(zone_id, {})
            items = queue_payload.get("data", {}).get("items", [])
            if items:
                events.append({
                    "zone_id": zone_id,
                    "zone_display_name": zone.get("display_name", ""),
                    "items": items,
                })
        return events

    def get_image_base64_payload(
        self,
        image_key: str,
        width: int = 400,
        height: int = 400,
    ) -> Dict[str, Any]:
        return self.zone_artwork.get_image_base64_payload(
            self.roon_connection, image_key, width, height,
        )

    def execute_roon_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Stale-cache recovery + feature_availability broadcast live
        # in the coordinator's dispatch path; this wrapper just forwards.
        return self.roon_control.execute(payload)

    def verify_stop_marker_availability(self) -> bool:
        """User-triggered re-check of the stop marker (waiting-state
        button click → ``feature-verify-request``). Runs the coordinator's
        full init walk and *always* broadcasts ``feature_availability``
        so the frontend can clear its in-flight indicator regardless
        of whether the available flag flipped. Without the unconditional
        broadcast, a marker-still-missing verify would silently skip
        the WS emission and the verifying spinner would only clear via
        client-side timeout. Returns the new ``available`` value. Safe
        to call before the coordinator exists (returns False)."""
        if self.stop_marker_coordinator is None:
            return False
        result = self.stop_marker_coordinator.initialise()
        self._broadcast_feature_availability()
        return result

    def get_stop_marker_available(self) -> bool:
        """Cached `available` flag from the coordinator.

        Returns False before the coordinator has been built (pre-Roon-
        init) and when the feature is disabled — the frontend treats
        both as "no real stop"; the disabled flag in the payload tells
        them apart for UI purposes (hide vs waiting state)."""
        if self.stop_marker_coordinator is None:
            return False
        return self.stop_marker_coordinator.available

    def get_feature_availability_payload(self) -> Dict[str, Any]:
        """Snapshot of feature-availability flags for the WS channel.

        LLM-provider check results live on the validation-status
        channel now (richer state machine, runtime-aware), so this
        payload focuses on Roon + required-config gating.
        """
        from app.settings import get_settings, required_config_missing
        from app.settings.endpoints import config_pristine
        from app.settings.validation import get_validator
        settings = get_settings()
        missing = required_config_missing(settings)
        tts_configured = bool(settings.tts_url)
        tts_available = tts_configured and _backend_ok(
            get_validator().current().backends, "tts",
        )
        return {
            "stop_marker_available": self.get_stop_marker_available(),
            "simulated_stop_disabled": settings.disable_simulated_stop,
            "stop_marker_title": settings.stop_marker_title,
            "config_complete": len(missing) == 0,
            "config_missing": missing,
            "config_pristine": config_pristine(),
            # Split so a flapping TCP target doesn't flicker the
            # "Not Configured" chip — that one tracks URL-set only;
            # ``tts_available`` carries the reachability state.
            "tts_configured": tts_configured,
            "tts_available": tts_available,
            "roon_state": self.roon_state,
            "roon_status_message": self.roon_status_message,
            "roon_failure_reason": self.roon_failure_reason,
            "roon_explorer_enabled": settings.enable_roon_explorer,
            # Desktop-bundle launch: gates bundle-only guidance (stop-marker
            # setup steps + the open-folder button) the browser shows.
            "is_bundle": _running_from_bundle(),
        }

    def _broadcast_feature_availability(self) -> None:
        from app.constants import CHANNEL_FEATURE_AVAILABILITY
        self._ws_send_callback(
            CHANNEL_FEATURE_AVAILABILITY,
            self.get_feature_availability_payload(),
        )

    def _handle_list_zones(self) -> Dict[str, Any]:
        return self.roon_control._handle_list_zones(self.roon_connection)

    def _handle_set_default_zone(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.roon_control._handle_set_default_zone(self.roon_connection, payload)

    @staticmethod
    def _resolve_agent_model(env_key: str, default: str = "") -> str:
        """Return the locked setting for *env_key*, falling back to
        *default* when unset/blank. Accepts ``LLM_MODEL`` and the two
        ``LLM_MODEL_<AGENT>`` overrides (arbiter, diagnostic)."""
        from app.settings import get_settings
        s = get_settings()
        mapping = {
            "LLM_MODEL": s.llm_model,
            "LLM_MODEL_ARBITER": s.llm_model_arbiter,
            "LLM_MODEL_DIAGNOSTIC": s.llm_model_diagnostic,
        }
        if env_key not in mapping:
            raise ValueError(f"Unknown agent model env key: {env_key!r}")
        return (mapping[env_key] or "").strip() or default

    @staticmethod
    def _parse_model_spec(spec: str) -> tuple[str, str]:
        """Parse a ``provider/model`` spec into (litellm_model, api_key).

        API key is looked up from the snapshotted
        ``LLM_API_KEY_<PROVIDER>`` map. Local providers like Ollama that
        don't need a real key return an empty string, which LiteLLM
        tolerates.
        """
        if "/" not in spec:
            raise ValueError(
                f"LLM model spec '{spec}' must be in provider/model format "
                "(e.g. 'anthropic/claude-sonnet-4-6').",
            )
        provider = spec.split("/", 1)[0]
        from app.settings import get_settings
        api_key = get_settings().api_key_for_provider(provider) or ""
        return spec, api_key

    # ── Prompt builders ───────────────────────────────────────────

    @staticmethod
    def build_coordinator_system_prompt() -> str:
        """Build the coordinator system prompt as a plain string.

        With native tool calling, the model decides which tool to use via
        the tool definitions — the system prompt focuses on behaviour,
        workflow patterns, and response format.
        """
        from app.settings import get_settings
        persona = get_settings().llm_persona or ""

        lines = [
            "You are Swarpius, a music assistant for Roon.",
        ]
        if persona:
            lines.append(f"Adopt the following persona: '{persona}'. Stay in character, but always prioritise accurate music control over character performance.")
        lines += [
            "",
            "## Behaviour",
            "- Each turn, call a tool OR reply to the user. Never both.",
            "- Prefer action over conversation. Only ask follow-ups when genuinely blocked by ambiguity.",
            "- Do not repeat successful work visible in prior tool results.",
            "- Do not fabricate information. When reporting outcomes, be accurate. If a request isn't possible due to capability limitations, or could not be fulfilled despite attempts, report the outcome accurately and describe the reasons.",
            "",
            "## Workflow",
            "- Break requests into parts. Work through each using the appropriate tool.",
            "- After each tool call, check results. If any part is unfulfilled, continue.",
            "- Playback requests require a successful roon_action result. Searching alone is not enough.",
            "- When the user references an item by number (e.g. 'track 5', 'item 3'), find the line numbered (5) or (3) in the listing and use its reference. Do not count items manually — match the number prefix directly.",
            "",
            "## Response style",
            "- Keep replies short and speech-friendly. Only text outside markup tags is spoken aloud via TTS.",
            (
                "- For long-form content (artist biographies, factual deep-dives, or any response too long to be comfortably spoken):"
                " wrap in `<extended_info><summary>Description</summary>...</extended_info>` tags."
                " The `<summary>` text becomes a clickable header; the body is displayed but not spoken."
            ),
            "- A response can combine both: a short spoken sentence plus a tag block for the full content.",
            "- Do not ask generic closers like 'want more music?'.",
        ]
        return "\n".join(lines)

    @staticmethod
    def build_arbiter_system_prompt() -> str:
        """Build the interrupt arbiter system prompt."""
        return "\n".join([
            "You decide whether a new websocket message should interrupt an active Swarpius request.",
            "",
            "Compare active_request and incoming_request, then return a JSON object with:",
            '  action: "queue" | "interrupt_and_replace" | "interrupt_only"',
            "  reason: one sentence",
            "  confidence: 0.0–1.0",
            "",
            "Rules:",
            "- queue: incoming is a follow-up, elaboration, near-identical to active, or can wait.",
            "- interrupt_and_replace: incoming is a new unrelated objective that supersedes active work.",
            "- interrupt_only: incoming is an explicit cancel/stop command.",
            "- Default to queue when uncertain.",
        ])

    def get_context_sections(self) -> List[Dict[str, str]]:
        """Collect dynamic context sections for prompt assembly.

        Returns a list of {title, content} dicts for non-empty providers.

        Order is graded by staleness so the static prefix (everything up
        to and including Current Date) can be cached separately from the
        dynamic tail. See TO_DO/improved-multiprovider-caching.md for the
        full rationale. Key Rules stays last so it keeps recency
        attention for functional corrections.
        """
        # Changes here also need analysis-guide.md updates — see docs/architecture.md
        providers = [
            # Static prefix — cacheable across requests
            self.skills_provider,
            self.zone_aliases_provider,
            self.current_date_provider,
            # Dynamic tail — changes per-call or per-turn
            self.current_time_provider,
            self.zone_status_provider,
            self.execution_trace_provider,
            self.search_history_provider,
            self.conversation_history_provider,
            self.key_rules_provider,  # Last — recency position in LLM context
        ]
        sections = []
        for p in providers:
            content = p.get_info()
            if content:
                sections.append({"title": p.title, "content": content})
        return sections

    def _persistence_participants(self) -> List["PersistentState"]:
        """The participants whose state this runtime persists. How many
        there are is an internal detail — callers go through
        ``attach_persistence``."""
        return [WorkingMemoryState(self)]

    def attach_persistence(self, manager: "PersistenceManager") -> None:
        """Apply any state saved by a previous run, then register for future
        saves. Restoring before registering keeps a fresh start (empty bag) a
        no-op."""
        for participant in self._persistence_participants():
            self._restore_and_register(manager, participant)

    def attach_roon_persistence(self, manager: "PersistenceManager") -> None:
        """Attach the Roon-connection-scoped participants (browse-session
        pool). Called once the Roon connection exists — its construction is
        later than the runtime's, so it registers separately from
        ``attach_persistence``. No-op if there is no connection."""
        if self.roon_connection is None:
            return
        self._restore_and_register(manager, self.roon_connection.session_manager)

    @staticmethod
    def _restore_and_register(
        manager: "PersistenceManager", participant: "PersistentState",
    ) -> None:
        saved = manager.restored_slice(participant.state_key)
        if saved is not None:
            participant.restore_state(saved)
        manager.register(participant)
