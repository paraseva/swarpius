import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exceptions import CategoryCorrectionFailed, UnsupportedActionError
from app.runtime.result_store_recovery import (
    AmbiguousTitleTie,
    FuzzyTitleWinner,
    NoTitleMatch,
    UniqueTitleMatch,
    lookup_category_gateway_for_reference,
    lookup_references_for_title,
    lookup_title_for_reference,
    recover_reference,
    titles_match,
)
from app.runtime.server_logger import get_server_logger
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreItemSummarySchema,
    RoonCoreResultsSchema,
)

_log = logging.getLogger("swarpius.roon_action")

IntendedItemCategory = str  # e.g. "track", "album", "playlist", or "auto"
RepeatModes = Literal["disabled", "loop", "loop_one"]
SeekMethods = Literal["absolute", "relative"]


_VALIDATE_ONLY_INTENTS = frozenset({"artist", "composer", "work"})


def _format_category_correction_error(
    item: RoonCoreItemSummarySchema,
    exc: CategoryCorrectionFailed,
) -> str:
    """Render a coordinator-actionable message for a category-correction
    miss. The reference resolved to the wrong shape and the reconciler
    couldn't (or wouldn't) auto-correct.

    Two retry-hint flavours:

    * **Validate-only intents** (artist / composer / work) — the
      resolved item isn't of the intended category. No auto-search by
      title (titles are usually lifted from a different-category item).
      Direct the coordinator to drill the category or re-search.
    * **Permissive container intents** (album / playlist) — track-→
      container reconciliation tried and failed. ``no_match`` retry hint
      means the category was present but no titled match; ``no_category``
      means the category itself was absent.
    """
    if exc.intended_category in _VALIDATE_ONLY_INTENTS:
        return (
            f"Item '{item.title}' (ref {item.reference}) is not "
            f"{_indefinite_article(exc.intended_category)} "
            f"{exc.intended_category}. Drill into the "
            f"'{exc.category_name}' category from the same search to "
            f"find the desired {exc.intended_category}, or re-search "
            f"if the {exc.intended_category} isn't in the results."
        )
    base = (
        f"Item '{item.title}' (ref {item.reference}) resolved as a track, "
    )
    track_retry = (
        "If you intended a track, retry with intended_item_category='track'."
    )
    if exc.failure_mode == "no_match":
        return (
            f"{base}but no {exc.intended_category} titled '{item.title}' "
            f"was found in the {exc.category_name} category. "
            f"{track_retry} Otherwise re-search and pick from the "
            f"{exc.category_name} category directly."
        )
    return (
        f"{base}and the search returned no matching "
        f"{exc.intended_category}s (no {exc.category_name} category "
        f"in the results). {track_retry} Otherwise re-search with "
        f"terms that would surface the {exc.intended_category}."
    )


def _indefinite_article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


# ── Matrix-rewrite shared helpers ─────────────────────────────────────

_CONTAINER_GATEWAYS = frozenset({"Play Album", "Play Playlist", "Play Work"})
# Persona gateways ("Play Artist" / "Play Composer") are classified via
# _persona_kind_for_gateway() below — no membership set needed.
_NOT_FOUND_TITLE = "Not Found"


def _article(category: str) -> str:
    return "an" if category[:1].lower() in "aeiou" else "a"


_CATEGORY_PLURAL_TITLE = {
    "artist": "Artists",
    "composer": "Composers",
    "album": "Albums",
    "playlist": "Playlists",
    "work": "Works",
    "track": "Tracks",
}


def _category_plural_title(category: str) -> str:
    """Capitalised plural form Roon uses in search-result subcategory
    tiles. Falls back to a simple title-case + 's' for unrecognised
    inputs (shouldn't happen via legitimate paths)."""
    return _CATEGORY_PLURAL_TITLE.get(category, category.capitalize() + "s")


def _persona_kind_for_gateway(gateway_title: str) -> Optional[str]:
    if gateway_title == "Play Artist":
        return "artist"
    if gateway_title == "Play Composer":
        return "composer"
    return None


def _container_kind_for_gateway(gateway_title: str) -> Optional[str]:
    mapping = {
        "Play Album": "album",
        "Play Playlist": "playlist",
        "Play Work": "work",
    }
    return mapping.get(gateway_title)


# ── Operator-facing message templates ──────────

def _msg_persona_rejection(ref: str, category: str, verb: str) -> str:
    return (
        f"{ref} is {_article(category)} {category}; {verb} cannot be "
        f"used with an artist or composer. To play their library, use "
        f"Play Now or Shuffle. To enqueue specific items, drill into "
        f"Albums or Tracks from the same search and pass those refs."
    )


def _msg_multi_persona_shuffle() -> str:
    return (
        "Shuffle accepts only one artist or composer at a time. To "
        "shuffle tracks across multiple artists or composers, drill "
        "into Albums from each search and pass relevant album refs to "
        "a single Shuffle call."
    )


def _msg_category_mismatch(ref: str, category: str) -> str:
    return (
        f"{ref} does not correspond to {_article(category)} {category}. "
        f"Drill into {_category_plural_title(category)} from the search "
        f"results to find an appropriate ref, or search again."
    )


def _msg_not_found(ref: str, title: str) -> str:
    return f"{ref} ('{title}') is currently unavailable on Roon - skipped."


# ── Browse primitives ──────────────────────────────────────────────────

ProbeCategory = Literal["container", "persona", "track", "not_found", "unknown"]


def _items_match(a: Any, b: Any) -> bool:
    """Four-field metadata equality used to detect a duplicate level —
    Roon sometimes interposes a child that mirrors the item we drilled
    into at every field except item_key (which discriminates the
    deeper level). title + subtitle + hint + image_key all matching is
    the reliable signal. Empty string and None are treated equivalently
    for the optional fields (Pydantic's RoonCoreItemSchema defaults
    subtitle to "" while ItemIdentity defaults it to None)."""
    def _norm(x: Optional[str]) -> Optional[str]:
        return x or None
    return (
        a.title == b.title
        and _norm(getattr(a, "subtitle", None)) == _norm(getattr(b, "subtitle", None))
        and _norm(getattr(a, "hint", None)) == _norm(getattr(b, "hint", None))
        and _norm(getattr(a, "image_key", None)) == _norm(getattr(b, "image_key", None))
    )


_CONTAINER_INTENTS = frozenset({"album", "playlist", "work"})


def _category_matches(
    category: str, sub_kind: Optional[str], intended: str,
) -> bool:
    """Whether a probed shape satisfies the coordinator's declared
    intended_category. Containers are interchangeable for intent
    purposes — drilling into any of album/playlist/work yields the
    same shape, so an intended 'album' is satisfied by any container
    sub_kind. Personas match their specific sub_kind."""
    if intended in _CONTAINER_INTENTS:
        return category == "container"
    if intended == "track":
        return category == "track"
    if intended == "artist":
        return category == "persona" and sub_kind == "artist"
    if intended == "composer":
        return category == "persona" and sub_kind == "composer"
    return True  # unknown intent — don't trigger reconciliation


def _classify_result_shape(
    result: Optional[RoonCoreResultsSchema],
) -> tuple[ProbeCategory, Optional[str]]:
    """Map a drill response to (category, sub_kind). Used by the
    reconciler-correction path to repackage corrected results as a
    fresh ProbeResult."""
    if not result or not result.items:
        return ("unknown", None)
    if result.list and result.list.hint == "action_list":
        return ("track", None)
    top = result.items[0]
    container_sub = _container_kind_for_gateway(top.title)
    if container_sub is not None:
        return ("container", container_sub)
    persona_sub = _persona_kind_for_gateway(top.title)
    if persona_sub is not None:
        return ("persona", persona_sub)
    if (
        top.title == "Not Found"
        and getattr(top, "subtitle", None) is None
        and getattr(top, "image_key", None) is None
    ):
        return ("not_found", None)
    return ("unknown", None)


@dataclass
class ProbeResult:
    """Outcome of probing a search reference: classification + the post-
    walk drill response + the path of item_keys consumed to reach it.

    ``results`` is None when the input itself was a track-shaped item
    (already at the action_list level — nothing was drilled). For
    every other category, ``results`` is the level reached when the
    probe stopped, with ``items[0]`` being the recognised gateway or
    Not Found marker.

    ``path`` is the sequence of item_keys traversed (including any
    duplicate-level drills); used by the expander to mint child refs
    that can be re-walked through the same session.

    ``ref`` caches the resolved StableReference so downstream callers
    (reconciler, expander) don't re-resolve — re-resolution can trigger
    semantic recovery if the reconciler mutated session state, which
    in turn fails noisily in fakes and adds latency in production.
    """

    category: ProbeCategory
    sub_kind: Optional[str]
    results: Optional[RoonCoreResultsSchema]
    path: List[str] = field(default_factory=list)
    levels_drilled: int = 0
    session_key: Optional[str] = None
    error: Optional[str] = None
    ref: Optional[Any] = None


ItemErrorBucket = Literal[
    "unknown_ref", "ambiguous_title", "no_title_match", "other"
]


def _classify_item_error(msg: str) -> ItemErrorBucket:
    """Classify a per-item ValueError message into a structured-error
    bucket. The caller appends the message (or its ref) to the list
    matching the returned bucket name.

    The four buckets map to four failure modes raised from
    `_execute_library_action_for_item` / `_apply_recovery_or_raise`:
    a ref not in search history; a ref unknown with no title rescue;
    a title-rescue tie; anything else.
    """
    lowered = msg.lower()
    if "reference" in lowered and "not found" in lowered:
        return "unknown_ref"
    if "ambiguous title, reference tied" in lowered:
        return "ambiguous_title"
    if "no title match" in lowered:
        return "no_title_match"
    return "other"

AllActions = Literal[
    # Library actions — verb-only matrix. The dispatcher classifies
    # the resolved ref's shape and applies the matrix; the coordinator
    # picks a verb regardless of category.
    "Play Now", "Add Next", "Queue", "Shuffle", "Start Radio",
    # Transport actions
    "play", "pause", "resume", "stop", "next", "previous",
    # Playback settings
    "set_shuffle", "set_repeat", "seek", "set_auto_radio",
    # Advanced controls
    "get_volume", "set_volume", "change_volume", "mute", "unmute",
    "pause_all", "standby", "convenience_switch",
    "mute_all", "unmute_all",
    # Queue controls
    "play_from_here",
]

LIBRARY_ACTIONS = {
    "Play Now", "Add Next", "Queue", "Shuffle", "Start Radio",
}
TRANSPORT_ACTIONS = {"play", "pause", "resume", "stop", "next", "previous"}
PLAYBACK_SETTINGS_ACTIONS = {"set_shuffle", "set_repeat", "seek", "set_auto_radio"}
ADVANCED_CONTROL_ACTIONS = {
    "get_volume", "set_volume", "change_volume", "mute", "unmute",
    "pause_all", "standby", "convenience_switch",
    "play_from_here", "mute_all", "unmute_all",
}
ZONE_OR_OUTPUT_REQUIRED_ACTIONS = {
    "get_volume", "set_volume", "change_volume", "mute", "unmute",
    "standby", "convenience_switch",
}

class RoonActionToolInputSchema(BaseModel):
    """Perform a Roon action: library play/queue, transport control, playback settings, or zone management."""

    action: AllActions = Field(
        ...,
        description="The action to perform",
    )
    zone: Optional[str] = Field(
        None,
        description="Target zone name",
    )
    # Library action fields
    items: List[RoonCoreItemSummarySchema] = Field(
        default_factory=list,
        description="Target media items for library actions.",
    )
    intended_item_category: IntendedItemCategory = Field(
        "auto",
        description=(
            "What the user intends to play — e.g. 'track', 'album', 'playlist'. "
            "Use 'auto' when intent is ambiguous."
        ),
    )
    # Playback settings fields
    shuffle: Optional[bool] = Field(
        None,
        description="Shuffle state for set_shuffle action",
    )
    repeat: Optional[RepeatModes] = Field(
        None,
        description="Repeat mode for set_repeat action",
    )
    seconds: Optional[int] = Field(
        None,
        description="Seek seconds for seek action",
    )
    seek_method: SeekMethods = Field(
        "absolute",
        description="Interpret seek seconds as absolute or relative",
    )
    # Advanced control fields
    output: Optional[str] = Field(
        None,
        description="Target output name (alternative to zone for output-level actions)",
    )
    volume: Optional[int] = Field(
        None,
        description="Absolute volume 0-100 for set_volume action",
    )
    delta: Optional[int] = Field(
        None,
        description="Relative volume delta for change_volume action",
    )
    queue_item_id: Optional[int] = Field(
        None,
        description=(
            "Queue item ID for play_from_here action. "
            "Look up the 5-char hex reference from the queue listing in "
            "queue_ref to resolve it, or pass the integer ID directly."
        ),
    )
    queue_ref: Optional[str] = Field(
        None,
        description=(
            "The Q:-prefixed reference from the queue listing "
            "(e.g. 'Q:a3f7c'). Resolved to queue_item_id automatically."
        ),
    )
    auto_radio: Optional[bool] = Field(
        None,
        description="Auto-radio state for set_auto_radio action",
    )
    count: Optional[int] = Field(
        None,
        description=(
            "For Shuffle only — limit to this many randomly selected tracks. "
            "Ignored for other actions."
        ),
    )

    @model_validator(mode="after")
    def validate_action_fields(self) -> "RoonActionToolInputSchema":
        if self.action in LIBRARY_ACTIONS:
            self._validate_library_action()
        elif self.action in PLAYBACK_SETTINGS_ACTIONS:
            self._validate_playback_settings_action()
        elif self.action in ADVANCED_CONTROL_ACTIONS:
            self._validate_advanced_control_action()
        return self

    def _validate_library_action(self) -> None:
        if not self.items:
            raise ValueError("'items' must be provided for library actions.")
        if self.action == "Add Next" and len(self.items) != 1:
            raise ValueError(
                "Add Next only accepts a single item. Use Queue to add "
                "multiple items.",
            )
        if self.action == "Start Radio" and len(self.items) != 1:
            raise ValueError(
                "Start Radio only accepts a single item. Choose an item "
                "or specify a different action.",
            )

    def _validate_playback_settings_action(self) -> None:
        required_by_action = {
            "set_shuffle":    ("shuffle",    self.shuffle),
            "set_repeat":     ("repeat",     self.repeat),
            "seek":           ("seconds",    self.seconds),
            "set_auto_radio": ("auto_radio", self.auto_radio),
        }
        field_name, value = required_by_action[self.action]
        if value is None:
            raise ValueError(f"{field_name} must be provided for {self.action} action")

    def _validate_advanced_control_action(self) -> None:
        if self.action == "set_volume" and self.volume is None:
            raise ValueError("volume must be provided for set_volume action")
        if self.action == "change_volume" and self.delta is None:
            raise ValueError("delta must be provided for change_volume action")
        if (
            self.action == "play_from_here"
            and self.queue_item_id is None
            and self.queue_ref is None
        ):
            raise ValueError(
                "queue_item_id or queue_ref must be provided for play_from_here action"
            )
        if self.action in ZONE_OR_OUTPUT_REQUIRED_ACTIONS and not self.zone and not self.output:
            raise ValueError("zone or output must be provided for this action")

class RoonActionErrorDetail(BaseModel):
    """Categorised error detail for library actions."""
    refs: List[str] = Field(description="References that caused this error")
    error: str = Field(description="Error description")


class RoonActionToolOutputSchema(BaseModel):
    """This schema describes the action performed."""

    zone: Optional[str] = Field(
        None,
        description="The zone in Roon where the action was attempted",
    )
    result: str = Field(..., description="Description of result of action")
    error: Optional[str] = Field(None, description="Error message if the action was not successful")
    errors: Optional[List[RoonActionErrorDetail]] = Field(
        None,
        description="Categorised error details for library actions. null when no errors.",
    )

class RoonActionToolConfig(BaseModel):

    model_config = ConfigDict(arbitrary_types_allowed=True)
    roon_connection: Optional[Any] = None
    # Required (no default): without a resolver the tool would have to
    # fall back to using the raw zone string, which silently masks
    # operator misconfiguration at the registration boundary.
    resolve_zone: Any = ...
    result_store: Optional[Any] = None
    cancel_event: Optional[Any] = None
    shutdown_event: Optional[Any] = None
    # Callable returning the live StopMarkerCoordinator (or None when
    # the runtime hasn't constructed one yet — e.g. early test wiring).
    # Passed as a getter rather than a direct ref so the tool picks up
    # the coordinator the runtime builds after Roon authorises.
    stop_marker_coordinator_getter: Optional[Any] = None


class RoonActionTool:
    """
    Tool for performing actions on Roon such as queueing / playing a library
    item or basic transport controls.
    """

    input_schema = RoonActionToolInputSchema
    output_schema = RoonActionToolOutputSchema
    parallel_safe = False

    def __init__(self, config: RoonActionToolConfig) -> None:
        self.config = config
        self.roon_connection = config.roon_connection
        self._resolve_zone = config.resolve_zone
        self._result_store = config.result_store if config.result_store is not None else {}
        self.cancel_event: Optional[Any] = config.cancel_event
        self._shutdown_event: Optional[Any] = config.shutdown_event
        self._get_stop_marker_coordinator = config.stop_marker_coordinator_getter

    def _resolve_queue_ref(self, ref: str) -> int:
        if ref.startswith("Q:"):
            ref = ref[2:]
        return self.roon_connection.resolve_queue_ref(ref)

    def _resolve(self, zone: Optional[str]) -> Optional[str]:
        """Resolve a zone name or alias to a real Roon zone display name."""
        if zone and self._resolve_zone:
            return self._resolve_zone(zone)
        return zone

    def _drill_down(
        self,
        item: Any,
        session_key: str,
        *,
        max_duplicates: int = 2,
    ) -> tuple[
        Optional[RoonCoreResultsSchema], List[str], int, Optional[str],
    ]:
        """Drill into *item* one level, consuming up to *max_duplicates*
        duplicate-mirror levels along the way.

        Refuses to drill items that would surface as a Not Found marker
        or that are `hint="action"` (which would execute the action
        rather than navigate).

        Returns (results, path_segment, levels_drilled, error). The
        path segment records the item_key of every drill performed,
        so the caller can mint refs that re-walk the same session
        position later.
        """
        if (
            item.title == "Not Found"
            and getattr(item, "subtitle", None) is None
            and getattr(item, "image_key", None) is None
        ):
            return (None, [], 0, "Unavailable item — cannot drill")
        if getattr(item, "hint", None) == "action":
            return (None, [], 0, "Action item — drilling would execute")
        if not item.item_key:
            return (None, [], 0, "Missing item_key — cannot drill")

        path: List[str] = []
        levels = 0
        drilled_into = item
        duplicates_consumed = 0
        results: Optional[RoonCoreResultsSchema] = None
        seen_keys: set[str] = set()

        while True:
            key = drilled_into.item_key
            if key in seen_keys:
                break  # self-loop guard
            seen_keys.add(key)
            results = self.roon_connection._nav_drill(
                key, session_key, update_current=False,
            )
            path.append(key)
            levels += 1

            if duplicates_consumed >= max_duplicates:
                break
            if not results.items:
                break
            top = results.items[0]
            if not _items_match(top, drilled_into):
                break
            drilled_into = top
            duplicates_consumed += 1

        return (results, path, levels, None)

    def _probe_item(
        self,
        ref_item: RoonCoreItemSummarySchema,
    ) -> ProbeResult:
        """Walk a search reference one level down (via :meth:`_drill_down`,
        which consumes any duplicate-mirror levels transparently) and
        classify what was reached: container (`Play Album` /
        `Play Playlist` / `Play Work` as items[0]), persona
        (`Play Artist` / `Play Composer` as items[0]), track terminal
        (`list.hint == "action_list"`), `Not Found` marker, or
        unknown (everything else — downstream code handles via the
        existing fallback paths).

        Returns silently with ``category="unknown"`` (no error) for
        unresolved refs so the downstream dispatch path's
        reference-recovery can fire. Sets ``error`` only when
        ``_drill_down`` itself reports one.
        """
        ref = self.roon_connection.resolve_reference(ref_item.reference)
        if ref is None or not ref.cached_item_key:
            return ProbeResult(
                category="unknown", sub_kind=None, results=None, ref=ref,
            )

        session_key = ref.roon_session_key
        synthesised = RoonCoreItemSchema(
            title=ref.identity.title,
            subtitle=getattr(ref.identity, "subtitle", None),
            hint=getattr(ref.identity, "hint", None),
            image_key=getattr(ref.identity, "image_key", None),
            item_key=ref.cached_item_key,
        )

        results, path, levels, error = self._drill_down(
            synthesised, session_key,
        )
        if error is not None or results is None:
            return ProbeResult(
                category="unknown", sub_kind=None, results=results,
                path=path, levels_drilled=levels,
                session_key=session_key, error=error, ref=ref,
            )

        if not results.items:
            return ProbeResult(
                category="unknown", sub_kind=None, results=results,
                path=path, levels_drilled=levels,
                session_key=session_key, ref=ref,
            )

        category, sub_kind = _classify_result_shape(results)
        return ProbeResult(
            category=category, sub_kind=sub_kind,
            results=results, path=path, levels_drilled=levels,
            session_key=session_key, ref=ref,
        )

    def _reconcile_probe(
        self,
        item: RoonCoreItemSummarySchema,
        probe: ProbeResult,
        intended_category: str,
    ) -> ProbeResult:
        """If the probed category doesn't match the caller's declared
        intent, invoke the reconciler against ``probe.results`` to walk
        to the corrected level. Returns a fresh ProbeResult for the
        corrected state; raises ``ValueError`` (with the operator-
        facing message) when reconciliation was attempted but no
        matching item could be found.

        Returns *probe* unchanged when no reconciliation is needed —
        the intent is "auto", already matches the probe, or the ref
        couldn't be resolved (downstream recovery handles)."""
        if not intended_category or intended_category == "auto":
            return probe
        if _category_matches(probe.category, probe.sub_kind, intended_category):
            return probe
        if probe.results is None or probe.session_key is None or probe.ref is None:
            return probe

        try:
            corrected = self.roon_connection.reconcile_intended_category(
                probe.ref, intended_category, probe.results, probe.session_key,
            )
        except CategoryCorrectionFailed as exc:
            raise ValueError(
                _format_category_correction_error(item, exc),
            ) from exc

        if corrected is None:
            return probe
        corrected_result, corrected_sk, _ = corrected
        if corrected_sk != probe.session_key:
            try:
                self.roon_connection._nav_reset_to_root(probe.session_key)
            except Exception as exc:
                # Best-effort cleanup of the abandoned session; a reset
                # blip must not block the corrected navigation flow.
                _log.debug(
                    "Failed to reset stale Roon navigation session",
                    exc_info=exc,
                )
        new_category, new_sub_kind = _classify_result_shape(corrected_result)
        # Reconciler called update_ref_key, so ref.item_key_path is now
        # the authoritative walkback for the corrected level. The
        # original probe.path is stale (positions in the old session)
        # — clear it so the expander doesn't append stale positions.
        return ProbeResult(
            category=new_category,
            sub_kind=new_sub_kind,
            results=corrected_result,
            path=[],
            levels_drilled=probe.levels_drilled,
            session_key=corrected_sk,
            ref=probe.ref,
        )

    def _expand_container_reference(
        self,
        item: RoonCoreItemSummarySchema,
        probe_result: Optional[ProbeResult] = None,
        intended_category: str = "auto",
    ) -> List[RoonCoreItemSummarySchema]:
        """Expand an item reference into its constituent tracks, using
        the pre-computed probe state.

        For track-shaped probe results (`list.hint == "action_list"`),
        returns ``[item]`` unchanged — tracks go into the Shuffle pool
        as-is. For container shapes, enumerates the post-probe items,
        skips gateway and Not Found entries, and mints a track ref for
        each remaining child.

        The matrix dispatcher reconciles intended_category UPSTREAM
        (via :meth:`_reconcile_probe`) and passes the corrected probe
        in; the expander itself only invokes the reconciler when
        called without a probe (legacy direct-call path).
        ``CategoryCorrectionFailed`` bubbles out as ``ValueError`` so
        the caller routes it via the standard per-item error bucket.
        """
        from roon_core.browse_session import ItemIdentity, SearchRecipe

        probe_provided = probe_result is not None
        if probe_result is None:
            probe_result = self._probe_item(item)

        ref = probe_result.ref or self.roon_connection.resolve_reference(
            item.reference,
        )
        if not ref or not ref.cached_item_key:
            return [item]

        result = probe_result.results
        session_key = probe_result.session_key or ref.roon_session_key

        if result is None:
            return [item]

        # Legacy direct-call path: reconcile in-place since the matrix
        # dispatcher's upstream reconciliation only fires when it
        # supplied the probe.
        probe_path_for_minting = list(probe_result.path)
        if not probe_provided:
            try:
                corrected = self.roon_connection.reconcile_intended_category(
                    ref, intended_category, result, session_key,
                )
            except CategoryCorrectionFailed as exc:
                raise ValueError(
                    _format_category_correction_error(item, exc),
                ) from exc
            if corrected:
                corrected_result, corrected_sk, _ = corrected
                if corrected_sk != session_key:
                    self.roon_connection._nav_reset_to_root(session_key)
                result = corrected_result
                session_key = corrected_sk
                # Reconciler updated ref.item_key_path to be the
                # authoritative walkback for the corrected level; the
                # probe.path positions are now stale (old session).
                probe_path_for_minting = []

        if result.list and result.list.hint == "action_list":
            self.roon_connection._nav_reset_to_root(session_key)
            return [item.model_copy(update={"intended_category": "track"})]

        # Self-loop: items[0] drills back to a key we've already
        # visited. Defensive degradation — fall back to the original
        # ref so dispatch tries its own walk via the walker.
        if (
            result.items
            and result.items[0].item_key
            and result.items[0].item_key in probe_result.path
        ):
            self.roon_connection._nav_reset_to_root(session_key)
            return [item]

        # Container — collect leaf children, skipping gateways and
        # Not Found markers.
        expanded: List[RoonCoreItemSummarySchema] = []
        parent_recipe = ref.recipe
        parent_path = list(ref.item_key_path)
        if not parent_path:
            container_pos = self.roon_connection._item_key_position(
                ref.cached_item_key,
            )
            if container_pos is not None:
                parent_path = [container_pos]
        # Append positions from any duplicate-wrapper drills the probe
        # consumed (probe.path[1:] — drilled_keys beyond the initial
        # cached_item_key drill). Both _reconcile_probe (matrix path)
        # and the legacy inline reconciler clear the path-for-minting
        # when correction fires, so this loop only runs in the
        # no-reconciliation case where wrapper levels are still part
        # of the same session.
        for drilled_key in probe_path_for_minting[1:]:
            pos = self.roon_connection._item_key_position(drilled_key)
            if pos is not None:
                parent_path.append(pos)

        for child in result.items:
            if child.title in _CONTAINER_GATEWAYS:
                continue
            if child.title == _NOT_FOUND_TITLE:
                continue
            # item_key_path stores position suffixes (e.g. '3' from '370:3'),
            # not full item_keys — _position_session uses _find_key_by_position
            # which matches by suffix.
            child_pos = self.roon_connection._item_key_position(child.item_key) if child.item_key else None
            child_path = parent_path + [child_pos] if child_pos else parent_path
            track_ref_id = self.roon_connection.session_manager.mint_ref(
                identity=ItemIdentity(
                    title=child.title,
                    subtitle=child.subtitle,
                    hint=child.hint,
                    image_key=child.image_key,
                ),
                recipe=SearchRecipe(
                    search_string=parent_recipe.search_string,
                    category=parent_recipe.category,
                    parent_chain=list(parent_recipe.parent_chain) + [ref.identity],
                ),
                item_key=child.item_key,
                session_key=session_key,
                item_key_path=child_path,
            )
            expanded.append(RoonCoreItemSummarySchema(
                title=child.title,
                reference=f"S:{track_ref_id}",
                group=item.title,
                extra_info=child.subtitle,
                # Pin category as 'track' so later action execution
                # doesn't inherit the request's intended_item_category
                # (e.g. 'album') and reconcile a legitimate track back
                # to an album — which would queue the whole album rather
                # than the one track.
                intended_category="track",
            ))

        self.roon_connection._nav_reset_to_root(session_key)
        return expanded

    def _category_gateway_error(
        self, item: RoonCoreItemSummarySchema,
    ) -> Optional[str]:
        """Return a coordinator-facing error if *item*'s reference
        points to a category-gateway entry (e.g. ``Tracks | 87 Results``)
        in the result store. ``None`` when the ref isn't a gateway or
        isn't in the store. No Roon calls.

        Without this guard, Shuffle silently expands gateways to all
        N children, and other verbs produce a generic "actions not
        found" error that doesn't point at the right next step.
        """
        if self._result_store is None:
            return None
        gateway = lookup_category_gateway_for_reference(
            self._result_store, item.reference,
        )
        if gateway is None:
            return None
        rendered = f"{gateway['title']} | {gateway['extra_info']}"
        return (
            f"{item.reference} ('{rendered}') is a category listing, "
            "not an actionable item. Drill into categories to reach "
            "specific items, then act on those references."
        )

    def _title_mismatch_error(
        self, item: RoonCoreItemSummarySchema,
    ) -> Optional[str]:
        """Return a user-facing error if *item*'s submitted title
        disagrees with the stored title for its reference. ``None``
        when titles match, the result store is unavailable, or the
        reference isn't in the store. No Roon calls — safe to run
        before any session-touching work.
        """
        if self._result_store is None:
            return None
        stored_title = lookup_title_for_reference(
            self._result_store, item.reference,
        )
        if stored_title is None or titles_match(item.title, stored_title):
            return None

        candidate_refs = lookup_references_for_title(
            self._result_store, item.title,
        )
        if not candidate_refs:
            # Avoid the substring "reference … not found" — the error
            # classifier in the caller uses that as the signature for the
            # "unknown reference" bucket and would strip this richer
            # message.
            title_match_clause = (
                f"title '{item.title}' doesn't appear in recent "
                f"search results"
            )
        elif len(candidate_refs) == 1:
            # Surface the stored title (titles_match is fuzzy — the
            # submitted title may differ from what's actually stored).
            matched_title = lookup_title_for_reference(
                self._result_store, candidate_refs[0],
            )
            title_match_clause = (
                f"title '{matched_title}' corresponds to reference "
                f"{candidate_refs[0]}"
            )
        else:
            refs_str = ", ".join(candidate_refs)
            title_match_clause = (
                f"title '{item.title}' corresponds to multiple "
                f"references in search results: {refs_str}"
            )

        get_server_logger().log(
            "action_title_ref_mismatch",
            submitted_title=item.title,
            stored_title=stored_title,
            reference=item.reference,
            candidate_refs=candidate_refs,
        )
        return (
            f"Title/reference mismatch: reference "
            f"{item.reference} corresponds to title "
            f"'{stored_title}'; {title_match_clause}. Select "
            f"the correct title/reference pair and re-submit, "
            f"or re-search if neither pair matches what you "
            f"intended."
        )

    def _get_actions(
        self,
        item: RoonCoreItemSummarySchema,
        intended_item_category: str = "auto",
    ) -> tuple[RoonCoreResultsSchema, str, int, Optional[str]]:
        """
        From a library item, resolve its reference and get available actions.
        Returns ``(results, session_key, levels_pushed, recovery_note)``.

        Two LLM-mistake guards run before the action proceeds:

        - **Title/reference mismatch.** If the submitted reference is
          present in the result store but the stored title disagrees
          with the submitted title, neither signal is trustworthy
          (could be right-title-wrong-ref or right-ref-wrong-title) —
          raise a clear error rather than play the wrong thing.
        - **Reference miss.** If the submitted reference fails to
          resolve at all, attempt a title-based fallback against the
          cached search results (handles LLM transcription slips on
          opaque hex IDs). See :mod:`app.result_store_recovery` for
          the decision rules; ``recovery_note`` is non-None only
          when recovery fired and the coordinator should be told
          what happened.
        """
        mismatch_error = self._title_mismatch_error(item)
        if mismatch_error is not None:
            raise ValueError(mismatch_error)

        try:
            results, session_key, levels_pushed = self.roon_connection.get_media_actions(
                media_item=item,
                intended_item_category=intended_item_category,
            )
        except CategoryCorrectionFailed as exc:
            raise ValueError(_format_category_correction_error(item, exc)) from exc

        if not results and not self.roon_connection.has_reference(item.reference):
            recovered_item, recovery_note = self._recover_from_reference_miss(
                item=item,
            )
            item = recovered_item
            try:
                results, session_key, levels_pushed = self.roon_connection.get_media_actions(
                    media_item=item,
                    intended_item_category=intended_item_category,
                )
            except CategoryCorrectionFailed as exc:
                raise ValueError(_format_category_correction_error(item, exc)) from exc
        else:
            recovery_note = None

        if not results:
            if not self.roon_connection.has_reference(item.reference):
                raise ValueError(
                    f"Reference '{item.reference}' not found. "
                    "Check that it matches a reference from the search results exactly."
                )
            if intended_item_category != "auto":
                raise ValueError(
                    f"Item '{item.title}' (ref {item.reference}) could not be resolved "
                    f"as a {intended_item_category}. The reference exists but resolution "
                    f"failed — try searching the {intended_item_category.capitalize()}s "
                    f"category directly."
                )
            raise ValueError(
                f"Item '{item.title}' (ref {item.reference}) could not be resolved. "
                "The reference exists but the item could not be reached — try a fresh search.",
            )

        if results.list and results.list.hint and results.list.hint != "action_list":
            raise ValueError(f"'{item.title}' cannot be played — actions not found.")

        get_server_logger().log(
            "action_get_actions",
            item_title=item.title,
            item_ref=item.reference,
            list_hint=results.list.hint if results.list else None,
            list_title=results.list.title if results.list else None,
            action_titles=[i.title for i in results.items],
            levels_pushed=levels_pushed,
            recovered=recovery_note is not None,
        )
        return results, session_key, levels_pushed, recovery_note

    def _recover_from_reference_miss(
        self,
        item: RoonCoreItemSummarySchema,
    ) -> tuple[RoonCoreItemSummarySchema, str]:
        """Attempt title-based recovery when ``item.reference`` doesn't
        resolve. Returns ``(effective_item, recovery_note)`` on success;
        raises ValueError with the right taxonomy on failure."""
        recovery = recover_reference(
            self._result_store,
            item.title,
            item.reference,
        )
        if isinstance(recovery, UniqueTitleMatch):
            get_server_logger().log(
                "action_reference_recovered",
                outcome="unique_title",
                submitted_ref=item.reference,
                recovered_ref=recovery.candidate.reference,
                title=item.title,
            )
            return (
                item.model_copy(update={"reference": recovery.candidate.reference}),
                (
                    f"reference mismatch, unique title match: "
                    f"'{item.title}' {item.reference} → {recovery.candidate.reference}"
                ),
            )
        if isinstance(recovery, FuzzyTitleWinner):
            get_server_logger().log(
                "action_reference_recovered",
                outcome="fuzzy_winner",
                submitted_ref=item.reference,
                recovered_ref=recovery.candidate.reference,
                title=item.title,
                best_distance=recovery.candidate.distance,
                runner_up_distance=recovery.runner_up_distance,
            )
            return (
                item.model_copy(update={"reference": recovery.candidate.reference}),
                (
                    f"reference mismatch, disambiguated title by closest reference: "
                    f"'{item.title}' {item.reference} → {recovery.candidate.reference} "
                    f"(distance {recovery.candidate.distance} vs "
                    f"{recovery.runner_up_distance})"
                ),
            )
        if isinstance(recovery, AmbiguousTitleTie):
            tied = ", ".join(c.reference for c in recovery.tied_candidates)
            raise ValueError(
                f"Ambiguous title, reference tied: '{item.title}' matches "
                f"{len(recovery.tied_candidates)} items in search history "
                f"({tied}) with equal distance to submitted reference "
                f"{item.reference}. Re-search or specify which one."
            )
        assert isinstance(recovery, NoTitleMatch)
        raise ValueError(
            f"Unknown reference '{item.reference}' and no title match for "
            f"'{item.title}' in search history. Check that the reference and "
            "title both come from recent search results."
        )

    def _execute_library_action_for_item(
        self,
        action: str,
        item: RoonCoreItemSummarySchema,
        zone: Optional[str],
        intended_item_category: str = "auto",
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Returns ``(success, error_msg, recovery_note)``.

        ``recovery_note`` is non-None when the submitted reference was
        recovered via title fallback (see ``_recover_from_reference_miss``).
        """
        actions, session_key, levels_pushed, recovery_note = self._get_actions(
            item=item, intended_item_category=intended_item_category,
        )
        requested_action = self.roon_connection.find_item_by_field(
            items=actions.items,
            field_name="title",
            field_value=action,
        )

        if not requested_action:
            # Clean up — reset to root so the session is usable for future items
            self.roon_connection._nav_reset_to_root(session_key, zone)
            available = [i.title for i in actions.items] if actions.items else []
            return (
                False,
                f"Requested action '{action}' not found for item '{item.title}'. "
                f"Available actions: {', '.join(available) if available else 'none'}.",
                recovery_note,
            )

        # Execute the action (session is still at the action menu level)
        get_server_logger().log(
            "action_execute",
            item_title=item.title,
            action=action,
            action_item_key=requested_action.item_key,
            session_key=session_key,
        )
        self.roon_connection.browse_core(
            aux={"item_key": requested_action.item_key},
            zone=zone,
            session_key=session_key,
            update_current=False,
        )

        # Roon's action execution auto-pops 1 level from the caller's
        # perspective (drill into action +1, Roon auto-pop -2 = net -1).
        # Adjust tracked depth to match actual before resetting to root,
        # because _nav_reset_to_root pops exactly the tracked depth and
        # Roon does not handle over-popping gracefully.
        depth = self.roon_connection.session_manager.get_session_depth(session_key)
        self.roon_connection.session_manager.set_session_depth(session_key, max(0, depth - 1))
        self.roon_connection._nav_reset_to_root(session_key, zone)
        get_server_logger().log("action_reset_to_root", session_key=session_key)

        return True, None, recovery_note

    def _transport_action(
        self,
        params: RoonActionToolInputSchema,
    ) -> RoonActionToolOutputSchema:

        # Roon's API doesn't expose a real `stop` — the native control
        # aliases to pause. We implement stop via a Play Now on a
        # user-installed silent marker track: queue gets replaced with
        # the silent track, plays sub-second, queue empties, playback
        # genuinely ends. Auto-radio is disabled first so it doesn't
        # kick in once the silent track is done.
        if params.action == "stop":
            return self._stop_action(params)

        action = params.action
        if action == "resume":
            action = "play"

        action_outcome = RoonActionToolOutputSchema(
            zone=params.zone,
            result=f"Transport action '{action}' ",
            error=None,
        )

        try:
            self.roon_connection.playback_control(control=action, zone=params.zone)
            action_outcome.result += "SUCCESSFUL"
        except Exception as exc:
            action_outcome.result += "FAILED"
            action_outcome.error = str(exc)

        return action_outcome

    def _stop_action(
        self,
        params: RoonActionToolInputSchema,
    ) -> RoonActionToolOutputSchema:
        coord = (
            self._get_stop_marker_coordinator()
            if self._get_stop_marker_coordinator else None
        )
        if coord is None:
            self.roon_connection.playback_control(
                control="pause", zone=params.zone,
            )
            return RoonActionToolOutputSchema(
                zone=params.zone,
                result="Transport action 'stop' SUCCESSFUL",
                error=None,
            )

        result = coord.dispatch_stop(zone=params.zone)
        if result.use_pause_fallback:
            self.roon_connection.playback_control(
                control="pause", zone=params.zone,
            )
            return RoonActionToolOutputSchema(
                zone=params.zone,
                result="Transport action 'stop' SUCCESSFUL",
                error=None,
            )
        if result.succeeded:
            return RoonActionToolOutputSchema(
                zone=params.zone,
                result="Transport action 'stop' SUCCESSFUL",
                error=None,
            )
        return RoonActionToolOutputSchema(
            zone=params.zone,
            result="Transport action 'stop' FAILED",
            error=result.error,
        )

    def _playback_settings_action(
        self,
        params: RoonActionToolInputSchema,
    ) -> RoonActionToolOutputSchema:
        action_outcome = RoonActionToolOutputSchema(
            zone=params.zone,
            result=f"Playback settings action '{params.action}' ",
            error=None,
        )

        try:
            if params.action == "set_shuffle":
                self.roon_connection.set_shuffle(shuffle=params.shuffle, zone=params.zone)
            elif params.action == "set_repeat":
                self.roon_connection.set_repeat(repeat=params.repeat, zone=params.zone)
            elif params.action == "seek":
                self.roon_connection.seek(
                    seconds=params.seconds,
                    method=params.seek_method,
                    zone=params.zone,
                )
            elif params.action == "set_auto_radio":
                self.roon_connection.set_auto_radio(
                    auto_radio=params.auto_radio,
                    zone=params.zone,
                )
            else:
                raise UnsupportedActionError(f"Unknown playback settings action '{params.action}'")
            action_outcome.result += "SUCCESSFUL"
        except Exception as exc:
            action_outcome.result += "FAILED"
            action_outcome.error = str(exc)

        return action_outcome

    def _advanced_control_action(
        self,
        params: RoonActionToolInputSchema,
    ) -> RoonActionToolOutputSchema:
        action_outcome = RoonActionToolOutputSchema(
            zone=params.zone,
            result=f"Advanced control action '{params.action}' ",
            error=None,
        )

        try:
            if params.action == "get_volume":
                volume = self.roon_connection.get_volume_percent(
                    zone=params.zone,
                    output=params.output,
                )
                action_outcome.result += f"SUCCESSFUL (volume={volume})"
            elif params.action == "set_volume":
                result = self.roon_connection.set_volume_percent(
                    volume=params.volume,
                    zone=params.zone,
                    output=params.output,
                )
                if result is not None:
                    transition = f"{result.previous_percent}% → {result.achieved_percent}%"
                    if result.achieved_percent != params.volume:
                        action_outcome.result += (
                            f"SUCCESSFUL ({transition}; device quantises to "
                            f"a coarser step than 1%)"
                        )
                    else:
                        action_outcome.result += f"SUCCESSFUL ({transition})"
                else:
                    action_outcome.result += "SUCCESSFUL"
            elif params.action == "change_volume":
                result = self.roon_connection.change_volume_percent(
                    delta=params.delta,
                    zone=params.zone,
                    output=params.output,
                )
                if result is not None:
                    action_outcome.result += (
                        f"SUCCESSFUL ({result.previous_percent}% → "
                        f"{result.achieved_percent}%)"
                    )
                else:
                    action_outcome.result += "SUCCESSFUL"
            elif params.action == "mute":
                self.roon_connection.mute(
                    mute=True,
                    zone=params.zone,
                    output=params.output,
                )
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "unmute":
                self.roon_connection.mute(
                    mute=False,
                    zone=params.zone,
                    output=params.output,
                )
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "pause_all":
                self.roon_connection.pause_all()
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "standby":
                self.roon_connection.standby(
                    zone=params.zone,
                    output=params.output,
                )
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "convenience_switch":
                self.roon_connection.convenience_switch(
                    zone=params.zone,
                    output=params.output,
                )
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "play_from_here":
                qid = params.queue_item_id
                if qid is None and params.queue_ref:
                    if params.queue_ref.startswith("S:"):
                        raise ValueError(
                            f"{params.queue_ref} is not a queue reference. "
                            'The "play_from_here" action only works with '
                            "queue items with Q:xxxxx references."
                        )
                    qid = self._resolve_queue_ref(params.queue_ref)
                self.roon_connection.play_from_here(
                    queue_item_id=qid,
                    zone=params.zone,
                )
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "mute_all":
                self.roon_connection.mute_all()
                action_outcome.result += "SUCCESSFUL"
            elif params.action == "unmute_all":
                self.roon_connection.unmute_all()
                action_outcome.result += "SUCCESSFUL"
            else:
                raise UnsupportedActionError(f"Unknown advanced control action '{params.action}'")
        except Exception as exc:
            action_outcome.result += "FAILED"
            action_outcome.error = str(exc)

        return action_outcome

    async def run_async(
        self, params: RoonActionToolInputSchema
    ) -> RoonActionToolOutputSchema:
        try:
            params.zone = self._resolve(params.zone)
        except Exception as exc:
            return RoonActionToolOutputSchema(
                zone=params.zone,
                result=f"Action '{params.action}' FAILED",
                error=str(exc),
            )

        if params.action in TRANSPORT_ACTIONS:
            return self._transport_action(params)
        if params.action in PLAYBACK_SETTINGS_ACTIONS:
            return self._playback_settings_action(params)
        if params.action in ADVANCED_CONTROL_ACTIONS:
            return self._advanced_control_action(params)

        # Library action — matrix-driven dispatch.
        zone = params.zone
        action_outcome = RoonActionToolOutputSchema(
            zone=zone,
            result=f"Library action '{params.action}' ",
            error=None
        )
        input_items = list(params.items)

        # Pre-flight: Play Now with >1 items rectifies to Queue. Queue's
        # auto-start-on-idle covers the "play the first one" UX.
        effective_action = params.action
        if effective_action == "Play Now" and len(input_items) > 1:
            effective_action = "Queue"

        execution_plan: List[tuple[str, RoonCoreItemSummarySchema]] = []
        # (reference, error_msg) pairs for items rejected before any
        # dispatch — Shuffle runs all-or-nothing on these.
        pre_dispatch_errors: List[tuple[str, str]] = []
        # Persona rejections per matrix (Add Next / Queue on persona).
        # (ref, message) pairs; routed to a dedicated error bucket.
        persona_rejections: List[tuple[str, str]] = []
        # Multi-persona Shuffle whole-call rejection — collected separately
        # because it produces one error with combined refs.
        multi_persona_shuffle_refs: List[str] = []
        # Informational notices (Not Found items) — per-item; never
        # bucketed as operator-actionable errors.
        not_found_notices: List[tuple[str, str]] = []

        # ── Probe every input item upfront ──────────────────────────
        # Title-mismatch check fires first (no Roon calls). The probe
        # then walks the ref through any intermediate navigation
        # levels until it reaches a recognised shape — saving the
        # downstream walker from re-resolving and re-drilling.
        probes: List[tuple[RoonCoreItemSummarySchema, ProbeResult]] = []
        for item in input_items:
            # Gateway check before mismatch — a gateway ref with a
            # mangled submitted title gets the "drill into categories"
            # message, not the less actionable mismatch one.
            pre_flight_error = (
                self._category_gateway_error(item)
                or self._title_mismatch_error(item)
            )
            if pre_flight_error is not None:
                probes.append((item, ProbeResult(
                    category="unknown", sub_kind=None, results=None,
                    error=pre_flight_error,
                )))
                continue
            probes.append((item, self._probe_item(item)))

        # ── Whole-call rejects ────────────────────────────────────
        if effective_action == "Shuffle":
            persona_refs = [
                it.reference for (it, p) in probes
                if p.category == "persona"
            ]
            if len(persona_refs) > 1:
                multi_persona_shuffle_refs = persona_refs
            elif len(persona_refs) == 1 and len(probes) > 1:
                # Mixed Shuffle: one persona + other items. The persona
                # is the offender; reject the whole call as a single
                # multi-persona-shape error (the message guides the
                # coordinator to drill the persona to album refs).
                multi_persona_shuffle_refs = persona_refs

        # ── Per-item matrix routing ───────────────────────────────
        # Shuffle accumulates non-persona items into a pool (tracks
        # contribute themselves; containers expand to their tracks).
        # The pool is then shuffled and dispatched as Play Now (first)
        # + Queue (rest). Personas under Shuffle dispatch native
        # Shuffle directly (no pool).
        shuffle_pool: List[RoonCoreItemSummarySchema] = []
        if not multi_persona_shuffle_refs:
            for item, probe in probes:
                if probe.error is not None:
                    pre_dispatch_errors.append(
                        (item.reference, probe.error),
                    )
                    continue
                if probe.category == "not_found":
                    not_found_notices.append((
                        item.reference,
                        _msg_not_found(item.reference, item.title),
                    ))
                    continue
                # Persona handling runs BEFORE reconciliation so the
                # persona-specific rejection message ("…can't be used
                # with an artist or composer…") takes precedence over
                # the generic category-mismatch message when an LLM
                # passes an artist ref tagged intended_category="album".
                if probe.category == "persona":
                    persona_kind = probe.sub_kind or "artist"
                    if effective_action in ("Add Next", "Queue"):
                        persona_rejections.append((
                            item.reference,
                            _msg_persona_rejection(
                                item.reference, persona_kind, effective_action,
                            ),
                        ))
                        continue
                    if effective_action == "Play Now":
                        execution_plan.append(("Shuffle", item))
                        continue
                    # Shuffle (single persona) / Start Radio — native
                    # dispatch via the persona's action_list.
                    execution_plan.append((effective_action, item))
                    continue
                # Reconcile if intent disagrees with probed category.
                # The reconciler walks to the corrected level (e.g.
                # track→album re-search) and we update the probe with
                # the new state; downstream code uses that.
                item_cat = (
                    item.intended_category
                    or params.intended_item_category
                )
                try:
                    probe = self._reconcile_probe(item, probe, item_cat)
                except ValueError as exc:
                    pre_dispatch_errors.append(
                        (item.reference, str(exc)),
                    )
                    continue
                if effective_action == "Shuffle":
                    # Track / container / unknown — all funnel through
                    # the shuffle pool. _expand_container_reference
                    # returns [item] for tracks and the leaf track list
                    # for containers. "unknown" shapes (fakes / legacy
                    # callers without explicit gateway) fall back to the
                    # same expander.
                    shuffle_pool.extend(
                        self._expand_container_reference(item, probe),
                    )
                    continue
                # Non-Shuffle verb on track / container / unknown:
                # existing per-item dispatch handles gateway navigation
                # and action lookup. Pass "auto" — reconciliation
                # already ran at this level if needed.
                execution_plan.append((effective_action, item))

            # Shuffle pool: randomise, optional count truncate, Play Now
            # first + Queue rest. All-or-nothing on pre_dispatch_errors:
            # the inputs were meant to combine into a single shuffled
            # pool; a partial pool is unrepresentative.
            if effective_action == "Shuffle":
                if pre_dispatch_errors:
                    execution_plan = []
                elif shuffle_pool:
                    random.shuffle(shuffle_pool)
                    if params.count is not None:
                        shuffle_pool = shuffle_pool[:params.count]
                    execution_plan.append(("Play Now", shuffle_pool[0]))
                    execution_plan.extend(
                        ("Queue", item) for item in shuffle_pool[1:]
                    )

        # Capture zone state before queueing so we can auto-start playback
        # when the user queues content onto an idle zone. Users saying "queue X"
        # on a stopped zone almost always expect playback to start — the Roon
        # API is literal about queue != play, so we bridge the gap here.
        # ``effective_action`` covers the Play Now → Queue rectify case too.
        was_stopped = False
        if effective_action == "Queue":
            try:
                snapshot = self.roon_connection.get_zone_snapshot(zone)
                was_stopped = (snapshot.get("state") or "").lower() == "stopped"
            except Exception:
                was_stopped = False

        successful = 0
        queue_ref_errors: List[str] = []
        unknown_ref_errors: List[str] = []
        unknown_ref_no_title_errors: List[str] = []
        ambiguous_title_errors: List[str] = []
        other_errors: List[str] = []
        recovery_notes: List[str] = []

        def _route_item_error(item_ref: Optional[str], msg: str) -> None:
            """Append a per-item error to the right structured-error list."""
            bucket = _classify_item_error(msg)
            if bucket == "unknown_ref":
                unknown_ref_errors.append(item_ref or msg)
            elif bucket == "ambiguous_title":
                ambiguous_title_errors.append(msg)
            elif bucket == "no_title_match":
                unknown_ref_no_title_errors.append(msg)
            else:
                other_errors.append(msg)

        for action_name, item in execution_plan:
            # Check for cancellation or server shutdown between items
            if (self.cancel_event and self.cancel_event.is_set()) or \
               (self._shutdown_event and self._shutdown_event.is_set()):
                break
            # Detect queue references used with library actions
            if item.reference and item.reference.startswith("Q:"):
                queue_ref_errors.append(item.reference)
                continue
            try:
                # Per-item category hint overrides the request-level category
                item_cat = item.intended_category or params.intended_item_category
                item_ok, item_error, recovery_note = self._execute_library_action_for_item(
                    action=action_name,
                    item=item,
                    zone=zone,
                    intended_item_category=item_cat,
                )
                if recovery_note:
                    recovery_notes.append(recovery_note)
                if item_ok:
                    successful += 1
                elif item_error:
                    _route_item_error(item.reference, item_error)
            except Exception as exc:
                _route_item_error(
                    item.reference,
                    f"Action failed for item '{item.title}': {exc}",
                )

        for ref, msg in pre_dispatch_errors:
            _route_item_error(ref, msg)

        structured_errors: List[RoonActionErrorDetail] = []
        if unknown_ref_errors:
            structured_errors.append(RoonActionErrorDetail(
                refs=unknown_ref_errors,
                error="Unknown reference(s)",
            ))
        if unknown_ref_no_title_errors:
            structured_errors.append(RoonActionErrorDetail(
                refs=[],
                error="; ".join(unknown_ref_no_title_errors),
            ))
        if ambiguous_title_errors:
            structured_errors.append(RoonActionErrorDetail(
                refs=[],
                error="; ".join(ambiguous_title_errors),
            ))
        if queue_ref_errors:
            structured_errors.append(RoonActionErrorDetail(
                refs=queue_ref_errors,
                error=(
                    "Q:xxxxx references are not search references. "
                    f'The "{params.action}" action only works with '
                    "search items with S:xxxxx references."
                ),
            ))
        if other_errors:
            structured_errors.append(RoonActionErrorDetail(
                refs=[],
                error="; ".join(other_errors),
            ))
        # Persona rejections (Add Next / Queue on persona) — each gets
        # its own error entry with the persona-specific guidance.
        for persona_ref, persona_msg in persona_rejections:
            structured_errors.append(RoonActionErrorDetail(
                refs=[persona_ref],
                error=persona_msg,
            ))
        # Multi-persona Shuffle whole-call reject — single error,
        # combined refs.
        if multi_persona_shuffle_refs:
            structured_errors.append(RoonActionErrorDetail(
                refs=list(multi_persona_shuffle_refs),
                error=_msg_multi_persona_shuffle(),
            ))
        # Not Found notices — informational; one per affected ref.
        for nf_ref, nf_msg in not_found_notices:
            structured_errors.append(RoonActionErrorDetail(
                refs=[nf_ref],
                error=nf_msg,
            ))

        # Fold rejections / notices into the total so a mixed request
        # reports PARTIAL SUCCESS rather than SUCCESSFUL alongside a
        # populated errors array. The numerator still counts only
        # execution-plan entries that played — detail lives in `errors`
        # and the top-line is an indicator.
        total = (
            len(execution_plan)
            + len(persona_rejections)
            + len(multi_persona_shuffle_refs)
            + len(not_found_notices)
            + len(pre_dispatch_errors)
        )
        if successful == total:
            action_outcome.result += (
                "SUCCESSFUL"
                if total == 1
                else f"SUCCESSFUL for all {total} items"
            )
        elif successful > 0:
            action_outcome.result += f"PARTIAL SUCCESS ({successful}/{total} items)"
        else:
            action_outcome.result += "FAILED"

        # Auto-start playback if we queued onto a previously stopped zone.
        if effective_action == "Queue" and successful > 0 and was_stopped:
            try:
                self.roon_connection.playback_control(control="play", zone=zone)
                action_outcome.result += " (zone was idle — playback started)"
            except Exception:
                # Best-effort UX — the queue already succeeded, the
                # auto-play is just a nicety. If transport rejects it
                # the user can press Play themselves.
                pass

        # Surface recovery notes so the coordinator sees that the LLM
        # mistyped a reference and it was recovered via title fallback.
        # Counts are useful for offline analysis of transcription drift.
        if recovery_notes:
            action_outcome.result += (
                f" [{len(recovery_notes)} reference(s) recovered via title: "
                + "; ".join(recovery_notes)
                + "]"
            )

        if structured_errors:
            action_outcome.errors = structured_errors

        return action_outcome


    def run(self, params: RoonActionToolInputSchema) -> RoonActionToolOutputSchema:
        with ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, self.run_async(params)).result()
