import logging
import re
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

from app.exceptions import ExternalServiceError, ZoneLookupError
from app.runtime.server_logger import get_server_logger
from roon_core.browse_session import (
    BrowseSessionManager,
    ItemIdentity,
    SearchRecipe,
    StableReference,
)
from roon_core.category_reconciler import CategoryReconciler
from roon_core.fuzzy_match import fuzzy_find, fuzzy_match_and_sort
from roon_core.image_fetch import fetch_image_bytes as _fetch_image_bytes
from roon_core.parallel_browse import install as install_parallel_browse
from roon_core.reference_walker import ReferenceWalker
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreItemSummarySchema,
    RoonCoreListSchema,
    RoonCoreResultsGroupSchema,
    RoonCoreResultsSchema,
)

_log = logging.getLogger("swarpius.browse")

_ROON_LINK_RE = re.compile(r"\[\[\d+\|([^\]]+)\]\]")


def strip_roon_links(text: str) -> str:
    """Convert Roon link markup ``[[ID|Name]]`` to plain ``Name``."""
    return _ROON_LINK_RE.sub(r"\1", text)


class RoonBrowseMixin:
    """Browse half of :class:`RoonConnection`. Not a standalone mixin —
    lives in its own module for navigability, composed only into
    :class:`RoonConnection` alongside the other Roon* mixins. Owns the
    browse-session lifecycle and reads zone state set up by the other
    halves."""

    def _init_browse_session(self, max_refs: int = 500) -> None:
        """Initialise browse session state. Called from RoonConnection.__init__."""
        self.session_manager = BrowseSessionManager(max_refs=max_refs)
        install_parallel_browse(self.api)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_browse_opts(
        self, zone: Optional[str], session_key: Optional[str],
    ) -> dict:
        try:
            zone_id = self._lookup_output_id(zone)
        except ZoneLookupError:
            # zone_or_output_id is just a browse-session anchor; the search
            # itself is library-wide. Fall back to any output only for the
            # implicit-default case — explicit-zone failures still propagate.
            if zone is not None:
                raise
            zone_id = next(
                (
                    output["output_id"]
                    for z in self.api.zones.values()
                    for output in z.get("outputs", [])
                    if output.get("output_id")
                ),
                None,
            )
            if zone_id is None:
                raise
            _log.warning(
                "Default zone %r is stale; using any-output fallback for browse",
                self.target_zone,
            )
        opts: dict = {"zone_or_output_id": zone_id, "hierarchy": "search"}
        if session_key:
            opts["multi_session_key"] = session_key
        return opts

    def _pop_levels(
        self, levels: int = 1, session_key: Optional[str] = None,
    ) -> None:
        opts = self._build_browse_opts(zone=None, session_key=session_key)
        self.api.browse_browse(opts | {"pop_levels": levels})

    def _duplicate_found(
        self, item: RoonCoreItemSchema, results: RoonCoreResultsSchema,
    ) -> bool:
        return (
            results.items
            and len(results.items) == 1
            and results.items[0].title == item.title
            and results.items[0].hint == item.hint
            and results.items[0].hint != "Action"
        )

    # ------------------------------------------------------------------
    # Core browse operations
    # ------------------------------------------------------------------

    @staticmethod
    def _search_retry_limit():
        from app.settings import get_settings
        return max(0, get_settings().roon_search_retry_limit)

    @staticmethod
    def _search_retry_delay():
        from app.settings import get_settings
        return max(0.0, get_settings().roon_search_retry_delay)

    def browse_core(
        self,
        aux: dict,
        zone: Optional[str] = None,
        session_key: Optional[str] = None,
        update_current: bool = True,
        max_items: Optional[int] = None,
    ) -> RoonCoreResultsSchema:
        if not aux:
            raise ValueError(
                "aux must contain item_key, pop_levels, input, or pop_all",
            )

        is_search = "input" in aux
        retry_limit = self._search_retry_limit() if is_search else 0
        max_attempts = retry_limit + 1

        retry_notes: list[str] = []
        for attempt in range(max_attempts):
            result = self._browse_core_once(aux, zone, session_key, max_items)
            if not is_search:
                break
            # Retry if Roon returned a transient empty search result
            if (
                len(result.items) == 1
                and result.items[0].title == "No Results"
                and attempt < max_attempts - 1
            ):
                note = (
                    f"Roon returned 'No Results' for input="
                    f"{aux.get('input')} — retrying ({attempt + 1}/{retry_limit})"
                )
                _log.info(note)
                retry_notes.append(note)
                time.sleep(self._search_retry_delay())
                continue
            break

        result.search_attempts = attempt + 1
        result.search_retry_notes = retry_notes or None

        if update_current:
            if not session_key:
                raise ValueError(
                    "session_key is required when update_current is True — "
                    "browse results must belong to an explicit session",
                )
            self.session_manager.set_current_list(session_key, result)
        return result

    def _browse_core_once(
        self,
        aux: dict,
        zone: Optional[str] = None,
        session_key: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> RoonCoreResultsSchema:
        opts = self._build_browse_opts(zone, session_key)

        try:
            browse_result = self.api.browse_browse(opts | aux)
        except Exception as exc:
            raise ExternalServiceError(
                f"Roon browse_browse failed (aux={aux}, zone={zone}, "
                f"session={session_key})",
            ) from exc
        # parallel_browse returns None on timeout / disconnect / send
        # failure rather than raising — surface it as a clean error
        # instead of letting browse_load proceed against an unprepared
        # session.
        if browse_result is None:
            raise ExternalServiceError(
                f"Roon browse_browse returned no response (aux={aux}, "
                f"zone={zone}, session={session_key}) — likely timed out "
                f"or socket disconnected",
            )

        raw = None
        try:
            all_items: List[RoonCoreItemSchema] = []
            offset = 0
            list_meta: Optional[RoonCoreListSchema] = None
            while True:
                load_opts = {**opts, "offset": offset, "count": 100}
                raw = self.api.browse_load(load_opts)
                if raw is None:
                    raise ExternalServiceError(
                        f"Roon browse_load returned no response "
                        f"(offset={offset}) — likely timed out or socket "
                        f"disconnected",
                    )
                page = RoonCoreResultsSchema.model_validate(raw)
                list_meta = page.list
                all_items.extend(page.items)
                total = page.list.count if page.list else len(page.items)
                if len(all_items) >= total:
                    break
                if max_items and len(all_items) >= max_items:
                    all_items = all_items[:max_items]
                    break
                offset = len(all_items)

            result = RoonCoreResultsSchema(items=all_items, list=list_meta)
            if result.list:
                result.list.count = len(all_items)
        except ExternalServiceError as exc:
            _log.error(
                "browse_load failed: aux=%s, zone=%s, session=%s: %s",
                aux, zone, session_key, exc,
            )
            raise
        except Exception as exc:
            _log.exception(
                "browse_load failed: aux=%s, zone=%s, session=%s, raw=%s",
                aux, zone, session_key, raw,
            )
            raise ExternalServiceError(
                f"Roon browse_load failed (aux={aux}, zone={zone}, "
                f"session={session_key}): {exc}",
            ) from exc

        get_server_logger().log(
            "browse_core",
            aux=aux,
            session_key=session_key,
            list_title=result.list.title if result.list else None,
            list_hint=result.list.hint if result.list else None,
            item_count=len(result.items),
            items=[(i.title, i.item_key, i.hint) for i in result.items],
        )

        return result

    def find_item_by_field(
        self,
        items: List[RoonCoreItemSchema],
        field_name: str,
        field_value: str,
        end_matches: bool = False,
    ) -> Optional[RoonCoreItemSchema]:
        if end_matches:
            for item in items:
                value = getattr(item, field_name, None)
                if isinstance(value, str) and value.lower().endswith(
                    field_value.lower(),
                ):
                    return item
            return None

        for item in items:
            value = getattr(item, field_name, None)
            if isinstance(value, str) and value.lower() == field_value.lower():
                return item
        return None

    # ------------------------------------------------------------------
    # Drill-down operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Navigation primitives — all browse navigation goes through these
    # ------------------------------------------------------------------

    def _nav_drill(
        self,
        item_key: str,
        session_key: str,
        zone: Optional[str] = None,
        update_current: bool = True,
    ) -> RoonCoreResultsSchema:
        """Drill into an item via browse_core. Increments session depth."""
        results = self.browse_core(
            aux={"item_key": item_key},
            zone=zone,
            session_key=session_key,
            update_current=update_current,
        )
        self.session_manager.set_session_depth(
            session_key,
            self.session_manager.get_session_depth(session_key) + 1,
        )
        return results

    def _nav_pop(
        self,
        levels: int,
        session_key: str,
        zone: Optional[str] = None,
    ) -> None:
        """Pop N levels. Decrements session depth. Does not load items."""
        opts = self._build_browse_opts(zone, session_key)
        self.api.browse_browse(opts | {"pop_levels": levels})
        depth = self.session_manager.get_session_depth(session_key)
        self.session_manager.set_session_depth(session_key, max(0, depth - levels))

    def _nav_reset_to_root(
        self,
        session_key: str,
        zone: Optional[str] = None,
    ) -> None:
        """Pop to root using tracked depth. Sets depth to 0.

        Uses the tracked session depth rather than a fixed large number
        because the Roon API does not handle excessively large pop_levels
        values correctly (observed: pop_levels=100 on a depth-1 session
        left the cursor at the wrong level).
        """
        depth = self.session_manager.get_session_depth(session_key)
        if depth > 0:
            opts = self._build_browse_opts(zone, session_key)
            self.api.browse_browse(opts | {"pop_levels": depth})
        self.session_manager.set_session_depth(session_key, 0)

    # ------------------------------------------------------------------
    # Drill-down operations
    # ------------------------------------------------------------------

    def drill_down(
        self,
        drilldown_item: RoonCoreItemSchema,
        sort_strings: Optional[List[str]] = None,
        recipe: Optional[SearchRecipe] = None,
        session_key: Optional[str] = None,
        update_current: bool = True,
    ) -> RoonCoreResultsSchema:
        if drilldown_item.item_key is None:
            raise ValueError(
                "item_key must be provided as reference for drill-down searches",
            )

        if not session_key:
            raise ValueError(
                "session_key is required for drill_down — each browse "
                "operation must target an explicit session.",
            )
        sk = session_key

        # Build parent path. For items with an existing path (set by a
        # prior drill_down), use it directly — it already includes the
        # item's own position as the last element. For root-level items
        # (no path yet), derive the position from the item_key.
        parent_path = list(drilldown_item.item_key_path or [])
        if not parent_path:
            pos = self._item_key_position(drilldown_item.item_key)
            if pos is not None:
                parent_path = [pos]

        results = self._nav_drill(
            drilldown_item.item_key, sk, update_current=update_current,
        )

        # Handle browse duplicate — a real Roon level where drilling into
        # an item yields a single-item list containing the same item again.
        # Its position is part of the path (it's a genuine browse level).
        if self._duplicate_found(item=drilldown_item, results=results):
            dup_pos = self._item_key_position(results.items[0].item_key)
            if dup_pos is not None:
                parent_path.append(dup_pos)
            results = self._nav_drill(
                results.items[0].item_key, sk, update_current=update_current,
            )

        # Set path on children: parent path + child's own position
        for item in results.items:
            item_pos = self._item_key_position(item.item_key)
            item.item_key_path = parent_path + ([item_pos] if item_pos else [])

        get_server_logger().log(
            "drill_down",
            item_title=drilldown_item.title,
            item_key=drilldown_item.item_key,
            parent_path=parent_path,
            result_count=len(results.items),
        )

        if sort_strings:
            results.items = fuzzy_match_and_sort(
                items=results.items, sort_strings=sort_strings,
            )

        return results

    # ------------------------------------------------------------------
    # Stable reference system
    # ------------------------------------------------------------------

    def compile_output(
        self,
        recipe: Optional[SearchRecipe] = None,
        session_key: Optional[str] = None,
    ) -> List[RoonCoreResultsGroupSchema]:
        """Package the session's current list into grouped summaries with
        stable references."""
        if not session_key:
            raise ValueError(
                "session_key is required for compile_output — each browse "
                "operation must target an explicit session.",
            )

        current = self.session_manager.get_current_list(session_key)
        if not current or not current.items:
            return []

        effective_recipe = recipe or SearchRecipe(search_string="")

        groupings: OrderedDict[str, List[RoonCoreItemSummarySchema]] = OrderedDict()

        for item in current.items:
            existing = self.session_manager.find_existing_ref(
                session_key, item.item_key,
            )
            if existing:
                ref_id = existing.ref_id
            else:
                identity = ItemIdentity(
                    title=item.title,
                    subtitle=item.subtitle,
                    hint=item.hint,
                    image_key=item.image_key,
                )
                ref_id = self.session_manager.mint_ref(
                    identity=identity,
                    recipe=effective_recipe,
                    item_key=item.item_key,
                    session_key=session_key,
                    item_key_path=item.item_key_path or [],
                )
            get_server_logger().log(
                "compile_ref",
                ref_id=ref_id,
                reused=existing is not None,
                title=item.title,
                hint=item.hint,
                item_key=item.item_key,
                item_key_path=item.item_key_path,
            )

            group_label = item.source_group or "-"
            extra_info = strip_roon_links(item.subtitle.strip()) if item.subtitle else ""

            if group_label not in groupings:
                groupings[group_label] = []

            groupings[group_label].append(
                RoonCoreItemSummarySchema(
                    title=item.title,
                    group=group_label,
                    extra_info=extra_info,
                    reference=f"S:{ref_id}",
                ),
            )

        return [
            RoonCoreResultsGroupSchema(group=label, items=items)
            for label, items in groupings.items()
        ]

    def has_reference(self, ref_id: str) -> bool:
        """Check whether a reference ID exists in the session manager."""
        if ref_id.startswith("S:"):
            ref_id = ref_id[2:]
        return self.session_manager.get_ref(ref_id) is not None

    def resolve_reference(
        self,
        ref_id: str,
        zone: Optional[str] = None,
        target_session: Optional[str] = None,
    ) -> Optional[StableReference]:
        """Resolve a reference and position its session at the parent level.

        After a successful call the session cursor sits at the level that
        produced the reference, so the caller can drill into the item with
        its ``cached_item_key``.

        Resolution tiers:
          1. Key is live on a known session → pop to root with pop_levels
             (non-destructive) and walk ``item_key_path[:-1]`` to reach the
             parent level.
          2. Semantic recovery — re-search on a dedicated recovery session
             and fuzzy-match to relocate the item.

        ``target_session`` forces a split: the reference's own session is held
        by a concurrent operation, so re-establish it on the leased session
        instead (re-search + walk), so the two never share one Roon cursor.
        """
        # Strip S: prefix (search references use this in coordinator output)
        if ref_id.startswith("S:"):
            ref_id = ref_id[2:]
        ref = self.session_manager.get_ref(ref_id)
        if not ref:
            get_server_logger().log("resolve_ref", ref_id=ref_id, found=False)
            return None

        slog = get_server_logger()
        slog.log(
            "resolve_ref_start",
            ref_id=ref_id,
            title=ref.identity.title,
            cached_item_key=ref.cached_item_key,
            session_key=ref.roon_session_key,
            item_key_path=ref.item_key_path,
        )

        # Split: re-establish on the leased session (a concurrent operation
        # holds the reference's own session). Re-search + walk, never the
        # shared cursor.
        if target_session is not None:
            if self._semantic_recover(ref, zone, session_key=target_session):
                slog.log("resolve_ref_done", ref_id=ref_id, tier="split", success=True)
                return ref
            slog.log("resolve_ref_done", ref_id=ref_id, tier="split_failed", success=False)
            return None

        # Tier 1: key is live on a known session — reposition non-destructively
        if self.session_manager.is_key_live(ref):
            try:
                self._position_session(ref, zone)
                slog.log("resolve_ref_done", ref_id=ref_id, tier="key_live", success=True)
                return ref
            except Exception as exc:
                slog.log(
                    "position_session_failed",
                    ref_id=ref_id,
                    session_key=ref.roon_session_key,
                    error=str(exc),
                )
                _log.warning(
                    "resolve_reference: positioning failed for ref=%s (%s), "
                    "falling through to semantic recovery",
                    ref.ref_id,
                    exc,
                )

        # Tier 2: semantic recovery (re-search + fuzzy-match)
        if self._semantic_recover(ref, zone):
            slog.log("resolve_ref_done", ref_id=ref_id, tier="semantic_recovery", success=True)
            return ref

        slog.log("resolve_ref_done", ref_id=ref_id, tier="failed", success=False)
        return None

    @staticmethod
    def _item_key_position(item_key: str) -> Optional[str]:
        """Extract the position suffix from an item_key like ``'1132:3'`` → ``'3'``."""
        parts = item_key.rsplit(":", 1)
        return parts[1] if len(parts) == 2 else None

    def _find_key_by_position(self, position: str, opts: dict) -> Optional[str]:
        """Load current items and return the full item_key at *position*.

        Uses the position hint to calculate the load offset so items
        beyond the first page (100) are reachable.
        """
        # Use position as offset hint — positions are typically numeric
        # and correspond roughly to the item's index in the list.
        try:
            pos_int = int(position)
        except (ValueError, TypeError):
            pos_int = 0
        offset = max(0, pos_int - 10)
        load_result = self.api.browse_load(opts | {"offset": offset, "count": 100})
        items = (load_result or {}).get("items", [])
        for item in items:
            key = item.get("item_key", "")
            if self._item_key_position(key) == position:
                return key
        # Fallback: if not found, try from the beginning (position might
        # not match the offset, e.g. after list mutations)
        if offset > 0:
            load_result = self.api.browse_load(opts | {"offset": 0, "count": 100})
            items = (load_result or {}).get("items", [])
            for item in items:
                key = item.get("item_key", "")
                if self._item_key_position(key) == position:
                    return key
        slog = get_server_logger()
        available = [
            (item.get("title", "?"), item.get("item_key", "?"))
            for item in items
        ]
        slog.log(
            "position_lookup_miss",
            wanted_position=position,
            item_count=len(items),
            available=available,
        )
        return None

    def _position_session(
        self,
        ref: StableReference,
        zone: Optional[str] = None,
    ) -> None:
        """Reset session to root and walk item_key_path to the parent level.

        Uses ``_nav_reset_to_root`` (pop 100 levels) to reach a guaranteed
        clean state, then drills via ``_nav_drill`` for each parent
        position.  Depth tracking is automatic through the nav primitives.
        """
        sk = ref.roon_session_key
        opts = self._build_browse_opts(zone, sk)

        # Reset to root — guaranteed clean state regardless of prior depth
        self._nav_reset_to_root(sk, zone)

        slog = get_server_logger()
        parent_positions = ref.item_key_path[:-1] if ref.item_key_path else []
        target_position = ref.item_key_path[-1] if ref.item_key_path else None
        slog.log(
            "position_session_start",
            ref_id=ref.ref_id,
            session_key=sk,
            parent_positions=parent_positions,
            target_position=target_position,
        )

        for i, pos in enumerate(parent_positions):
            fresh_key = self._find_key_by_position(pos, opts)
            slog.log("position_walk_step", index=i, position=pos, fresh_key=fresh_key)
            if not fresh_key:
                raise LookupError(
                    f"Cannot find item at position {pos} during path walk "
                    f"(ref={ref.ref_id}, step={i})",
                )
            self._nav_drill(fresh_key, sk, zone, update_current=False)

        if target_position is not None:
            fresh_target = self._find_key_by_position(target_position, opts)
            if fresh_target:
                ref.cached_item_key = fresh_target
        slog.log(
            "position_done",
            ref_id=ref.ref_id,
            cached_item_key=ref.cached_item_key,
        )

    def _match_level_item(
        self,
        items: List[RoonCoreItemSchema],
        position: Optional[str],
        identity: ItemIdentity,
    ) -> Optional[RoonCoreItemSchema]:
        """Locate an item at a re-searched level. Prefer the item at the
        recorded ``position`` when its identity still matches — the only way to
        separate items identical in title/subtitle/image_key — otherwise fall
        back to the best identity match. ``None`` if neither matches."""
        if position is not None:
            at_position = next(
                (
                    item
                    for item in items
                    if self._item_key_position(item.item_key) == position
                ),
                None,
            )
            if at_position is not None and fuzzy_find([at_position], identity):
                return at_position
        return fuzzy_find(items, identity)

    def _semantic_recover(
        self,
        ref: StableReference,
        zone: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> bool:
        """Re-search and fuzzy-match to relocate the item, re-establishing it on
        ``session_key`` (a leased split session) or, by default, a freshly
        leased session. The default path is a fallback that should not fire in
        normal operation — hence the warning; a split is intentional, so it is
        silent. Either way the session is private to this call, so concurrent
        recoveries never share a cursor."""
        if not ref.recipe.search_string:
            return False

        sk = session_key or self.session_manager.new_search_session()
        if session_key is None:
            _log.warning(
                "Semantic recovery triggered for ref=%s (%s) — "
                "this indicates a fast-path bug if it happens during normal operation",
                ref.ref_id,
                ref.identity.title,
            )

        try:
            results = self.browse_core(
                aux={"pop_all": True, "input": ref.recipe.search_string},
                zone=zone,
                session_key=sk,
                update_current=False,
            )
        except ExternalServiceError:
            return False
        # Re-search put the cursor at the search root. Track depth from here via
        # _nav_drill (as drill_down does) so a ref re-established on this session
        # can later reset to root and walk its path on the fast path, instead of
        # falling back into recovery again.
        self.session_manager.set_session_depth(sk, 0)

        if ref.recipe.category:
            cat_item = self.find_item_by_field(
                results.items, "title", ref.recipe.category,
            )
            if not cat_item:
                return False
            try:
                results = self._nav_drill(
                    cat_item.item_key, sk, zone, update_current=False,
                )
            except ExternalServiceError:
                return False

        path = ref.item_key_path
        position_path: List[str] = []
        for index, ancestor in enumerate(ref.recipe.parent_chain):
            expected = path[index] if index < len(path) - 1 else None
            match = self._match_level_item(results.items, expected, ancestor)
            if not match:
                return False
            pos = self._item_key_position(match.item_key)
            if pos is not None:
                position_path.append(pos)
            try:
                results = self._nav_drill(
                    match.item_key, sk, zone, update_current=False,
                )
            except ExternalServiceError:
                return False

        target = self._match_level_item(
            results.items, path[-1] if path else None, ref.identity,
        )
        if not target:
            return False

        target_pos = self._item_key_position(target.item_key)
        if target_pos is not None:
            position_path.append(target_pos)
        self.session_manager.update_ref_key(ref, target.item_key, sk, position_path)
        return True


    def reconcile_intended_category(
        self,
        ref: "StableReference",
        intended_category: str,
        current_results: RoonCoreResultsSchema,
        session_key: str,
        zone: Optional[str] = None,
    ) -> Optional[Tuple[RoonCoreResultsSchema, str, int]]:
        """Delegate to :class:`CategoryReconciler`. Kept on the mixin
        so callers reach it through the RoonConnection facade exactly
        as before."""
        return CategoryReconciler(self).reconcile(
            ref, intended_category, current_results, session_key, zone,
        )

    def get_media_actions(
        self,
        media_item: RoonCoreItemSummarySchema,
        zone: Optional[str] = None,
        intended_item_category: str = "auto",
    ) -> Tuple[Optional[RoonCoreResultsSchema], Optional[str], int]:
        """Delegate to :class:`ReferenceWalker`. Kept on the mixin so
        callers reach it through the RoonConnection facade."""
        return ReferenceWalker(self).get_media_actions(
            media_item, zone, intended_item_category,
        )


    # ------------------------------------------------------------------
    # Image fetching
    # ------------------------------------------------------------------

    def fetch_image_bytes(
        self,
        image_key: str,
        width: int = 400,
        height: int = 400,
    ) -> tuple[bytes, str]:
        """Delegate to :func:`roon_core.image_fetch.fetch_image_bytes`.
        Kept on the mixin so callers reach it through the RoonConnection
        facade without needing to thread host/port themselves."""
        return _fetch_image_bytes(
            self.api,
            self.roon_core_host,
            self.roon_core_port,
            image_key,
            width,
            height,
        )
