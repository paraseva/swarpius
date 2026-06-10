"""Category reconciliation: correct a category mismatch between
what the user intended and what Roon's resolver returned.

Extracted from ``RoonBrowseMixin``. The reconciler is a small class
that takes a browse facade (any object exposing ``browse_core``,
``_nav_drill``, ``_nav_reset_to_root``, ``_item_key_position``,
``find_item_by_field``, and ``session_manager``) and runs the
correction algorithm against it. Production passes the live
``RoonBrowseMixin`` instance; tests pass a ``BrowseFake`` with
scripted browse responses.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from app.exceptions import CategoryCorrectionFailed, ExternalServiceError
from app.runtime.server_logger import get_server_logger
from roon_core.browse_session import StableReference
from roon_core.fuzzy_match import normalise_title
from roon_core.schemas import RoonCoreItemSchema, RoonCoreResultsSchema

# Module-level constants — also imported by browse.py's
# _pick_drill_target, which uses GATEWAY_CATEGORY_MAP to decide
# whether items[0] is a gateway item before drilling.
GATEWAY_CATEGORY_MAP = {
    "Play Album": "album",
    "Play Playlist": "playlist",
    "Play Artist": "artist",
    "Play Composer": "composer",
    "Play Work": "work",
}
_CATEGORY_TO_GATEWAY = {
    "album": "Play Album",
    "playlist": "Play Playlist",
    "composer": "Play Composer",
    "work": "Play Work",
}
_CATEGORY_NAMES = {
    "album": "Albums",
    "playlist": "Playlists",
    "artist": "Artists",
    "composer": "Composers",
    "work": "Works",
}
# Terminal action_list titles Roon exposes for personas (artist /
# composer). Containers (album / playlist / work) expose Play Now /
# Add Next / Queue, which never appear in a persona action_list.
_PERSONA_ACTION_SIGNATURE = {"Shuffle", "Start Radio"}
_PERSONA_INTENTS = frozenset({"artist", "composer"})


class CategoryReconciler:
    """Walks the corrected-category path when the resolved item doesn't
    match the intended category. Handles three directions:

    * **album → track** (gateway sibling search): at a gateway level
      whose category doesn't match the intent, find the matching track
      among sibling items and drill into it.
    * **track → album** (category re-search): at a track action list
      (or a single-child wrapper around one) when an album was intended,
      re-search via the Albums category and navigate to the matched
      album's container level.
    * **anything → artist** (validate-only): when an artist was
      intended, the resolved item must terminate at the
      ``{Shuffle, Start Radio}`` action_list signature, which only
      artists produce. Auto-correction isn't possible (the title is
      usually wrong-item-derived, so we can't search for the right
      artist), so failure raises :class:`CategoryCorrectionFailed`.
    """

    def __init__(self, browse: Any) -> None:
        self._browse = browse

    def reconcile(
        self,
        ref: StableReference,
        intended_category: str,
        current_results: RoonCoreResultsSchema,
        session_key: str,
        zone: Optional[str] = None,
    ) -> Optional[Tuple[RoonCoreResultsSchema, str, int]]:
        """Returns ``(results, session_key, levels_pushed)`` positioned
        at the corrected item, or ``None`` if no correction is needed.
        Raises :class:`CategoryCorrectionFailed` when correction was
        attempted but no matching item was found in the intended
        category — the caller should surface a retry hint rather than
        silently fall through to the (wrong-category) original ref.
        """
        if intended_category == "auto":
            return None

        items = current_results.items or []
        list_hint = current_results.list.hint if current_results.list else None

        # Persona intents (artist, composer) have a validate-only path:
        # check the gateway and terminal-action_list signatures, fail
        # loud if either doesn't match. Bypasses the album/playlist
        # gateway-sibling case below.
        if intended_category in _PERSONA_INTENTS:
            return self._validate_persona_intent(
                ref, items, list_hint, intended_category,
            )

        # Work intent has gateway-only validation (terminal signature
        # is shared with album, so no action_list-level discriminator).
        if intended_category == "work":
            return self._validate_work_intent(ref, items)

        # Case 1: gateway mismatch — e.g. "Play Album" but wanted track
        if items:
            gateway_category = GATEWAY_CATEGORY_MAP.get(items[0].title)
            if gateway_category and gateway_category != intended_category:
                return self._correct_via_gateway_siblings(
                    current_results, ref, session_key, zone,
                )

        # Determine if we're looking at a track (directly or wrapped).
        # Roon sometimes returns a wrapper level with list_hint=null
        # containing a single child with hint=action_list — this is
        # effectively a track, not a container.
        is_track = (
            list_hint == "action_list"
            or (
                list_hint != "action_list"
                and len(items) == 1
                and getattr(items[0], "hint", None) == "action_list"
            )
        )

        # Case 2: track (or track wrapper) but wanted album — re-search
        if is_track:
            expected_gateway = _CATEGORY_TO_GATEWAY.get(intended_category)
            list_title = current_results.list.title if current_results.list else None
            if (
                expected_gateway
                and list_title != expected_gateway
                and ref.recipe.search_string
            ):
                return self._correct_via_category_search(
                    ref, intended_category, zone,
                )

        return None

    def _validate_persona_intent(
        self,
        ref: StableReference,
        items: List[RoonCoreItemSchema],
        list_hint: Optional[str],
        intended_category: str,
    ) -> Optional[Tuple[RoonCoreResultsSchema, str, int]]:
        """Validate that the resolved item is a persona (artist or
        composer). Both share the terminal ``{Shuffle, Start Radio}``
        action_list signature.

        Fails loud at a non-persona gateway (``Play Album`` /
        ``Play Playlist`` / ``Play Work``) or at a terminal
        ``action_list`` whose titles aren't a subset of the persona
        signature. Passes (returns ``None``) at intermediate levels —
        including the persona's own gateway (``Play Artist`` /
        ``Play Composer``) — so the walk progresses and re-validates
        at the next iteration.

        Validate-only: no auto-correction, since we have no reliable
        signal for which artist/composer the caller actually meant.
        """
        if items:
            actual_gateway_cat = GATEWAY_CATEGORY_MAP.get(items[0].title)
            if actual_gateway_cat and actual_gateway_cat != intended_category:
                raise CategoryCorrectionFailed(
                    ref_id=ref.ref_id,
                    title=ref.identity.title,
                    intended_category=intended_category,
                    category_name=_CATEGORY_NAMES[intended_category],
                    failure_mode="no_match",
                )
        if list_hint == "action_list":
            action_titles = {i.title for i in items}
            if not action_titles.issubset(_PERSONA_ACTION_SIGNATURE):
                raise CategoryCorrectionFailed(
                    ref_id=ref.ref_id,
                    title=ref.identity.title,
                    intended_category=intended_category,
                    category_name=_CATEGORY_NAMES[intended_category],
                    failure_mode="no_match",
                )
        return None

    def _validate_work_intent(
        self,
        ref: StableReference,
        items: List[RoonCoreItemSchema],
    ) -> Optional[Tuple[RoonCoreResultsSchema, str, int]]:
        """Validate that the resolved item is a work.

        Work shares its terminal action_list signature with album, so
        validation can only fire at the gateway level: items[0] must
        be the ``Play Work`` gateway, or some non-gateway shape (where
        the walk can still progress). Any other gateway (``Play
        Album`` / ``Play Playlist`` / ``Play Artist`` / ``Play
        Composer``) fails loud.
        """
        if items:
            actual_gateway_cat = GATEWAY_CATEGORY_MAP.get(items[0].title)
            if actual_gateway_cat and actual_gateway_cat != "work":
                raise CategoryCorrectionFailed(
                    ref_id=ref.ref_id,
                    title=ref.identity.title,
                    intended_category="work",
                    category_name=_CATEGORY_NAMES["work"],
                    failure_mode="no_match",
                )
        return None

    def _correct_via_gateway_siblings(
        self,
        results: RoonCoreResultsSchema,
        ref: StableReference,
        session_key: str,
        zone: Optional[str] = None,
    ) -> Optional[Tuple[RoonCoreResultsSchema, str, int]]:
        """Find a matching track among gateway siblings and drill into it.

        At a gateway level (e.g. "Play Album" as the first item, tracks
        listed below), search the sibling items for one whose normalised
        title matches the reference identity. Drills into the match and
        returns its action-list results.
        """
        slog = get_server_logger()
        target_title = normalise_title(ref.identity.title)
        match = next(
            (i for i in results.items[1:]
             if normalise_title(i.title) == target_title),
            None,
        )
        if not match:
            slog.log(
                "category_correction_no_sibling",
                ref_id=ref.ref_id,
                target=ref.identity.title,
                sibling_titles=[i.title for i in results.items[1:5]],
            )
            return None

        slog.log(
            "category_correction_sibling_match",
            ref_id=ref.ref_id,
            matched_title=match.title,
            matched_key=match.item_key,
        )
        corrected = self._browse._nav_drill(
            match.item_key, session_key, zone, update_current=False,
        )
        return corrected, session_key, 1

    def _correct_via_category_search(
        self,
        ref: StableReference,
        intended_category: str,
        zone: Optional[str] = None,
    ) -> Optional[Tuple[RoonCoreResultsSchema, str, int]]:
        """Re-search and navigate via the correct category.

        When the resolved item is the wrong category (e.g. a track when
        an album was intended), re-search using the ref's recipe on the
        recovery session, drill into the matching category (e.g. Albums),
        find the best title match, and return results at the container
        level (e.g. [Play Album, track1, track2, ...]).

        The caller can continue drilling from there — ``get_media_actions``
        will pick "Play Album" via ``_pick_drill_target``, while
        ``_expand_reference`` will enumerate children.
        """
        category_name = _CATEGORY_NAMES.get(intended_category)
        if not category_name:
            return None

        slog = get_server_logger()
        sk = self._browse.session_manager.recovery_session_key
        search_string = ref.recipe.search_string

        slog.log(
            "category_correction_search",
            ref_id=ref.ref_id,
            intended=intended_category,
            search_string=search_string,
        )

        try:
            results = self._browse.browse_core(
                aux={"pop_all": True, "input": search_string},
                zone=zone,
                session_key=sk,
                update_current=False,
            )
        except ExternalServiceError:
            return None

        # Find and drill into the target category (e.g. "Albums").
        # Missing category is a "tried and failed" outcome, same class
        # as the no-strict-match path below — raise rather than return
        # None so the caller surfaces a retry hint instead of silently
        # falling through to the (wrong-category) original ref. The
        # session was just reset by browse_core(pop_all=True) so no
        # explicit reset needed before raising.
        cat_item = self._browse.find_item_by_field(
            results.items, "title", category_name,
        )
        if not cat_item:
            slog.log(
                "category_correction_no_category",
                ref_id=ref.ref_id,
                category=category_name,
                available=[i.title for i in results.items],
            )
            raise CategoryCorrectionFailed(
                ref_id=ref.ref_id,
                title=ref.identity.title,
                intended_category=intended_category,
                category_name=category_name,
                failure_mode="no_category",
            )

        levels_pushed = 0
        # Track positions of each drilled key so the parent ref can be
        # re-pointed at the corrected container. Callers that mint child
        # refs (e.g. ``_expand_reference``) build child item_key_path
        # from the parent's position — without this, the children would
        # walk the wrong route in the recovery session.
        position_path: List[str] = []
        cat_pos = self._browse._item_key_position(cat_item.item_key)
        if cat_pos is not None:
            position_path.append(cat_pos)
        results = self._browse._nav_drill(
            cat_item.item_key, sk, zone, update_current=False,
        )
        levels_pushed += 1

        # Strict normalised-title equality only. A substring fallback used
        # to live here, but it would silently grab unrelated hits — e.g. a
        # "Voices" track ref intended as an album would match a karaoke
        # compilation whose title merely contained the word "voices".
        # Failing loud here lets the caller surface a clear retry hint
        # instead of playing the wrong thing.
        target_title = normalise_title(ref.identity.title)
        best_match = next(
            (i for i in results.items
             if normalise_title(i.title) == target_title),
            None,
        )

        if not best_match:
            slog.log(
                "category_correction_no_match",
                ref_id=ref.ref_id,
                target=ref.identity.title,
                available=[i.title for i in results.items],
            )
            self._browse._nav_reset_to_root(sk, zone)
            raise CategoryCorrectionFailed(
                ref_id=ref.ref_id,
                title=ref.identity.title,
                intended_category=intended_category,
                category_name=category_name,
                failure_mode="no_match",
            )

        slog.log(
            "category_correction_match",
            ref_id=ref.ref_id,
            matched_title=best_match.title,
            matched_key=best_match.item_key,
        )

        # Drill into the matched item — stop at container level
        match_pos = self._browse._item_key_position(best_match.item_key)
        if match_pos is not None:
            position_path.append(match_pos)
        container_item_key = best_match.item_key
        results = self._browse._nav_drill(
            best_match.item_key, sk, zone, update_current=False,
        )
        levels_pushed += 1

        # Handle disambiguation (all children hint='list') — pick first
        if results.items and all(
            getattr(c, "hint", None) == "list" for c in results.items
        ):
            disambig = results.items[0]
            disambig_pos = self._browse._item_key_position(disambig.item_key)
            if disambig_pos is not None:
                position_path.append(disambig_pos)
            container_item_key = disambig.item_key
            results = self._browse._nav_drill(
                disambig.item_key, sk, zone, update_current=False,
            )
            levels_pushed += 1

        # Re-point the parent ref at the corrected container so callers
        # mint child refs whose paths walk through the recovery session.
        self._browse.session_manager.update_ref_key(
            ref, container_item_key, sk, position_path,
        )

        slog.log(
            "category_correction_done",
            ref_id=ref.ref_id,
            final_hint=results.list.hint if results.list else None,
            final_title=results.list.title if results.list else None,
            item_titles=[i.title for i in (results.items or [])],
            levels_pushed=levels_pushed,
        )

        return results, sk, levels_pushed
