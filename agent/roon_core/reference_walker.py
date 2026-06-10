"""Walk a stable reference to its action list.

Given a media item with a stable reference, ``ReferenceWalker``
resolves the reference, drills through any intermediate levels
(duplicates, gateways, variant groupings), and stops at the
``list.hint == "action_list"`` level where the caller can dispatch
a concrete action (Play Now, Add Next, etc.).

Category mismatches between intent and resolved item are corrected
transparently by delegating to :class:`CategoryReconciler` once per
loop iteration. Drill-target selection is local
(``_pick_drill_target``).

Extracted from ``RoonBrowseMixin``. Takes a browse facade — any
object exposing ``resolve_reference``, ``_nav_drill``,
``reconcile_intended_category``, and ``_duplicate_found`` — in its
constructor. Production passes the ``RoonBrowseMixin`` instance.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from app.runtime.server_logger import get_server_logger
from roon_core.browse_session import StableReference
from roon_core.category_reconciler import GATEWAY_CATEGORY_MAP
from roon_core.fuzzy_match import fuzzy_find
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreItemSummarySchema,
    RoonCoreResultsSchema,
)

# Soft ceiling on the drill loop. The deepest legitimate Roon path
# is ~4 levels (Albums → Albums-by-artist → Album → action_list);
# 6 leaves headroom without letting a pathological case spin.
_MAX_ACTION_DRILL_DEPTH = 6


class ReferenceWalker:
    """Resolve a stable reference and drill to its action list."""

    def __init__(self, browse: Any) -> None:
        self._browse = browse

    def get_media_actions(
        self,
        media_item: RoonCoreItemSummarySchema,
        zone: Optional[str] = None,
        intended_item_category: str = "auto",
    ) -> Tuple[Optional[RoonCoreResultsSchema], Optional[str], int]:
        """Returns ``(results, session_key, levels_pushed)`` positioned
        at the action list, or ``(None, None, 0)`` on failure. After
        executing the chosen action the caller should call
        ``_nav_reset_to_root`` to leave the session clean.
        """
        import logging
        _log = logging.getLogger("swarpius.browse")

        ref = self._browse.resolve_reference(media_item.reference, zone=zone)
        if not ref or not ref.cached_item_key:
            return None, None, 0

        sk = ref.roon_session_key
        levels_pushed = 0

        _log.debug(
            "get_media_actions: ref=%s identity=%s hint=%s item_key=%s",
            ref.ref_id, ref.identity.title, ref.identity.hint, ref.cached_item_key,
        )

        slog = get_server_logger()

        # Initial drill into the referenced item
        results = self._browse._nav_drill(
            ref.cached_item_key, sk, zone, update_current=False,
        )
        levels_pushed += 1

        for depth in range(_MAX_ACTION_DRILL_DEPTH):
            # Transparent category reconciliation — corrects mismatches
            # (e.g. track→album or album→track) before any other logic.
            corrected = self._browse.reconcile_intended_category(
                ref, intended_item_category, results, sk, zone,
            )
            if corrected:
                results, sk, extra = corrected
                levels_pushed += extra

            list_hint = results.list.hint if results.list else None
            list_title = results.list.title if results.list else None
            item_titles = [i.title for i in (results.items or [])]
            item_hints = [i.hint for i in (results.items or [])]
            item_keys = [i.item_key for i in (results.items or [])]
            _log.debug(
                "get_media_actions: depth=%d list_hint=%s items=%s item_hints=%s",
                depth, list_hint, item_titles, item_hints,
            )
            slog.log(
                "get_actions_depth",
                ref_id=ref.ref_id,
                depth=depth,
                list_hint=list_hint,
                list_title=list_title,
                item_titles=item_titles,
                item_hints=item_hints,
                item_keys=item_keys,
            )

            if list_hint == "action_list":
                break

            if not results.items:
                _log.warning("get_media_actions: no items at depth %d", depth)
                break

            next_item = self._pick_drill_target(results, ref, intended_item_category)
            if not next_item:
                _log.warning(
                    "get_media_actions: no drill target at depth %d", depth,
                )
                break

            _log.debug(
                "get_media_actions: drilling into '%s' (hint=%s)",
                next_item.title, next_item.hint,
            )
            slog.log(
                "get_actions_drill",
                ref_id=ref.ref_id,
                depth=depth,
                target_title=next_item.title,
                target_hint=next_item.hint,
                target_item_key=next_item.item_key,
            )
            results = self._browse._nav_drill(
                next_item.item_key, sk, zone, update_current=False,
            )
            levels_pushed += 1

        final_hint = results.list.hint if results.list else None
        final_title = results.list.title if results.list else None
        final_titles = [i.title for i in (results.items or [])]
        _log.debug(
            "get_media_actions: final list_hint=%s items=%s levels_pushed=%d",
            final_hint, final_titles, levels_pushed,
        )
        slog.log(
            "get_actions_done",
            ref_id=ref.ref_id,
            final_list_hint=final_hint,
            final_list_title=final_title,
            action_titles=final_titles,
            levels_pushed=levels_pushed,
        )

        return results, sk, levels_pushed

    def _pick_drill_target(
        self,
        results: RoonCoreResultsSchema,
        ref: StableReference,
        intended_item_category: str = "auto",
    ) -> Optional[RoonCoreItemSchema]:
        """Choose which item to drill into on the way to an action list.

        Handles duplicates (single item matching identity), gateways
        ("Play Album" etc.), and variant groupings (all items hint
        ``action_list``).

        At gateway items, *intended_item_category* determines whether to
        drill into the gateway (category matches or is ``auto``) or
        return ``None`` so the caller can invoke
        ``reconcile_intended_category``.
        """
        items = results.items
        slog = get_server_logger()
        if not items:
            slog.log("pick_drill_target", reason="no_items", chosen=None)
            return None

        # Single item matching the target identity — duplicate level
        temp_item = RoonCoreItemSchema(
            title=ref.identity.title,
            hint=ref.identity.hint,
        )
        if self._browse._duplicate_found(item=temp_item, results=results):
            slog.log("pick_drill_target", reason="duplicate", chosen=items[0].title)
            return items[0]

        # Gateway items (e.g. "Play Playlist", "Play Album")
        gateway_category = GATEWAY_CATEGORY_MAP.get(items[0].title)
        if gateway_category:
            if intended_item_category in (gateway_category, "auto"):
                slog.log(
                    "pick_drill_target",
                    reason="gateway_match",
                    gateway=items[0].title,
                    intent=intended_item_category,
                    chosen=items[0].title,
                )
                return items[0]

            # Mismatch — caller handles via reconcile_intended_category
            slog.log(
                "pick_drill_target",
                reason="gateway_mismatch",
                gateway=items[0].title,
                intent=intended_item_category,
                chosen=None,
            )
            return None

        # All items share the same hint — variant recordings
        # (action_list) or version disambiguation (list).
        hints = {getattr(i, "hint", None) for i in items}
        if len(hints) == 1 and hints <= {"action_list", "list"}:
            chosen = fuzzy_find(items, ref.identity) or items[0]
            slog.log("pick_drill_target", reason="uniform_group", chosen=chosen.title)
            return chosen

        slog.log(
            "pick_drill_target",
            reason="no_match",
            item_titles=[i.title for i in items],
            chosen=None,
        )
        return None
