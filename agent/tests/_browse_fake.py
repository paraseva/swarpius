"""Shared fake browse layer for tests that exercise RoonBrowseMixin.

The fake inherits ``RoonBrowseMixin`` and stubs only the ``browse_core``
API boundary plus a handful of zone/transport recorders. Production
methods (``resolve_reference``, ``get_media_actions``,
``reconcile_intended_category``, ``drill_down``, ``_pick_drill_target``,
``_validate_persona_intent``, ``_validate_work_intent``, the persona/work
gateway maps, the duplicate/wrapper detector, ``find_item_by_field``,
etc.) all run live against the fake's primitives.

Tests describe a scenario by registering items via
``register_item(ref_id, title, ...)``. Each call mints a real
``StableReference`` in a real ``BrowseSessionManager`` and records the
shape of the drill response Roon would return for that item:

* ``action_titles=[...]`` → terminal action_list; ``items[0]`` won't be
  a recognised gateway, so the production reconciler validates against
  the action_list signature.
* ``gateway="Play Album"`` (or any of the recognised gateway titles) →
  drill yields a gateway-shaped level; production drills further if the
  intent matches, or raises ``CategoryCorrectionFailed`` via the work/
  persona validators if it doesn't. ``gateway_action_titles`` controls
  what the *next* drill (into the gateway item) yields.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from roon_core.browse import RoonBrowseMixin
from roon_core.browse_session import (
    BrowseSessionManager,
    ItemIdentity,
    SearchRecipe,
    StableReference,
)
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)

_DEFAULT_ALBUM_ACTIONS = ["Play Now", "Add Next", "Queue", "Start Radio"]


class BrowseFake(RoonBrowseMixin):
    """Fake Roon connection that runs production browse logic against
    a scripted ``browse_core``.

    See module docstring for the registration model.
    """

    def __init__(self) -> None:
        self.session_manager = BrowseSessionManager()
        # Single shared session for all registered items — keeps tests
        # from worrying about multi-session state. Production is
        # multi-session capable but the recovery/intent tests don't need
        # to exercise that.
        self._session_key = self.session_manager.new_search_session()
        self.session_manager.set_session_depth(self._session_key, 1)

        self.api = SimpleNamespace(zones={}, outputs={})

        # item_key → drill response. Anything not in here is treated as
        # an action-execution call (return empty action_list).
        self._drill_responses: Dict[str, RoonCoreResultsSchema] = {}

        # search_input → list of items the search returns. Used by
        # tests that exercise the search→drill flow rather than the
        # pre-minted-ref flow.
        self._search_responses: Dict[str, List[RoonCoreItemSchema]] = {}

        # Recorders for assertions
        self.browse_aux_calls: List[dict] = []
        self.browse_zones: List[Optional[str]] = []
        self.action_dispatches: List[dict] = []
        self.playback_calls: List[dict] = []
        self.set_auto_radio_calls: List[dict] = []
        self.shuffle_calls: List[bool] = []
        self.repeat_calls: List[str] = []
        self.seek_calls: List[dict] = []
        self.volume_get_calls: int = 0
        self.set_volume_calls: List[int] = []
        self.change_volume_calls: List[int] = []
        self.mute_calls: List[bool] = []
        self.pause_all_calls: int = 0
        self.standby_calls: int = 0
        self.convenience_switch_calls: int = 0

        # Reverse-map of action item_key → (action_title, ref_id) so
        # tests can assert on the (action, ref) sequence without
        # reverse-engineering the synthetic item_key format.
        self._action_lookup: Dict[str, tuple[str, str]] = {}

        # Configurable zone state — ``get_zone_snapshot`` reports this.
        self.zone_state: str = "playing"

    # ------------------------------------------------------------------
    # Setup API
    # ------------------------------------------------------------------

    def register_item(
        self,
        ref_id: str,
        title: str,
        *,
        action_titles: Optional[List[str]] = None,
        gateway: Optional[str] = None,
        gateway_action_titles: Optional[List[str]] = None,
        item_hint: Optional[str] = "action_list",
    ) -> str:
        """Register an item that production code can resolve and drill.

        Mints a ``StableReference`` keyed by *ref_id* in the session
        manager (so ``has_reference("S:" + ref_id)`` returns True and
        ``resolve_reference`` succeeds). Records a drill response that
        production's ``get_media_actions`` will see when it drills the
        item.

        Use exactly one of:

        * ``action_titles`` — drill yields a terminal action_list. Items
          take ``hint="Action"`` and ``items[0]`` won't match any
          gateway title, so the persona validator only kicks in via the
          action_list signature check.
        * ``gateway`` — drill yields a gateway-level result with
          ``items[0].title=gateway``. ``gateway_action_titles`` (default
          ``["Play Now", "Add Next", "Queue", "Start Radio"]``) controls
          what drilling *into* the gateway item yields.

        Returns the bare ref_id (without the ``S:`` prefix).
        """
        if (action_titles is None) == (gateway is None):
            raise ValueError(
                "register_item: pass exactly one of action_titles or gateway",
            )

        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        item_key = f"item-{bare}"

        identity = ItemIdentity(title=title, hint=item_hint)
        recipe = SearchRecipe(search_string=title)
        # Insert directly so the test controls the ref_id (mint_ref
        # generates random ones).
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=identity,
            recipe=recipe,
            cached_item_key=item_key,
            roon_session_key=self._session_key,
            item_key_path=[],  # empty → _position_session does nothing
        )

        if action_titles is not None:
            self._drill_responses[item_key] = self._make_action_list(
                action_titles, list_title=title, ref_id=bare,
            )
        else:
            assert gateway is not None
            gateway_key = f"gateway-{gateway}-{bare}"
            self._drill_responses[item_key] = RoonCoreResultsSchema(
                items=[
                    RoonCoreItemSchema(
                        title=gateway,
                        item_key=gateway_key,
                        # Real Roon emits hint="action_list" on the
                        # single-child gateway level (the matrix shape
                        # classifier depends on this exact shape).
                        hint="action_list",
                    ),
                ],
                # A gateway level isn't a terminal action_list (production
                # drills further). list_hint is null.
                list=RoonCoreListSchema(
                    count=1, hint=None, title=title,
                ),
            )
            self._drill_responses[gateway_key] = self._make_action_list(
                gateway_action_titles or _DEFAULT_ALBUM_ACTIONS,
                list_title=title, ref_id=bare,
            )

        return bare

    def register_track(self, ref_id: str, title: str) -> str:
        """Convenience: a track ref whose drill yields the standard
        track action_list (``Play Now`` / ``Add Next`` / ``Queue`` /
        ``Start Radio``)."""
        return self.register_item(
            ref_id, title,
            action_titles=_DEFAULT_ALBUM_ACTIONS,
        )

    def register_container(
        self,
        ref_id: str,
        title: str,
        child_titles: List[str],
        *,
        include_play_album_gateway: bool = False,
    ) -> str:
        """Container (album/playlist): drill yields a list of child items
        with ``hint='action_list'`` (each child is a track). When
        ``include_play_album_gateway`` is True, the first item is a
        ``Play Album`` gateway — used to test gateway-skip logic in
        ``_expand_container_reference`` and gateway-mismatch reconciliation."""
        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        item_key = f"item-{bare}"
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=ItemIdentity(title=title, hint="list"),
            recipe=SearchRecipe(search_string=title),
            cached_item_key=item_key,
            roon_session_key=self._session_key,
            item_key_path=[],
        )

        items: List[RoonCoreItemSchema] = []
        if include_play_album_gateway:
            items.append(
                RoonCoreItemSchema(
                    title="Play Album",
                    item_key=f"gateway-Play Album-{bare}",
                    hint="Action",
                ),
            )
        for i, child_title in enumerate(child_titles):
            child_key = f"track-key-{bare}-{i}"
            items.append(
                RoonCoreItemSchema(
                    title=child_title,
                    item_key=child_key,
                    hint="action_list",
                ),
            )
            # Register the track's own drill response so that when the
            # tool resolves a minted child ref and drills into it for
            # action dispatch, it sees the standard track action_list.
            self._drill_responses[child_key] = self._make_action_list(
                _DEFAULT_ALBUM_ACTIONS,
                list_title=child_title, ref_id=f"{bare}-{i}",
            )
        self._drill_responses[item_key] = RoonCoreResultsSchema(
            items=items,
            list=RoonCoreListSchema(count=len(items), hint="list", title=title),
        )
        return bare

    def register_artist(self, ref_id: str, title: str) -> str:
        """Artist ref with the degenerate (zero reachable children)
        shape: drill yields a single ``Play Artist`` child with
        ``list_hint=null``. Drilling the gateway yields ``[Shuffle,
        Start Radio]``. For an artist with reachable children, use
        ``register_persona_with_children`` instead."""
        return self.register_item(
            ref_id, title,
            gateway="Play Artist",
            gateway_action_titles=["Shuffle", "Start Radio"],
        )

    def register_album_with_versions(
        self,
        ref_id: str,
        title: str,
        version_track_titles: List[List[str]],
    ) -> str:
        """Album with multi-version disambiguation: drill yields a list
        of version entries (``hint='list'``); drilling the first version
        yields its track list (``hint='list'``, children are tracks).
        ``version_track_titles`` is one list of track titles per
        version."""
        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        item_key = f"item-{bare}"
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=ItemIdentity(title=title, hint="list"),
            recipe=SearchRecipe(search_string=title),
            cached_item_key=item_key,
            roon_session_key=self._session_key,
            item_key_path=[],
        )
        version_items = []
        for v_idx, tracks in enumerate(version_track_titles):
            version_key = f"version-key-{bare}-{v_idx}"
            version_items.append(
                RoonCoreItemSchema(
                    title=title, item_key=version_key, hint="list",
                ),
            )
            track_items = []
            for ti, t in enumerate(tracks):
                track_key = f"track-key-{bare}-{v_idx}-{ti}"
                track_items.append(
                    RoonCoreItemSchema(
                        title=t, item_key=track_key, hint="action_list",
                    ),
                )
                # Track's own action_list (for action dispatch on
                # minted child refs).
                self._drill_responses[track_key] = self._make_action_list(
                    _DEFAULT_ALBUM_ACTIONS,
                    list_title=t, ref_id=f"{bare}-{v_idx}-{ti}",
                )
            self._drill_responses[version_key] = RoonCoreResultsSchema(
                items=track_items,
                list=RoonCoreListSchema(
                    count=len(track_items), hint="list", title=title,
                ),
            )
        self._drill_responses[item_key] = RoonCoreResultsSchema(
            items=version_items,
            list=RoonCoreListSchema(
                count=len(version_items), hint="list", title=title,
            ),
        )
        return bare

    def register_drill(
        self, item_key: str, response: RoonCoreResultsSchema,
    ) -> None:
        """Low-level escape hatch: register an arbitrary drill response
        for a specific item_key. Used for self-loop wrappers and other
        unusual shapes that the higher-level helpers don't cover."""
        self._drill_responses[item_key] = response

    def register_persona_with_children(
        self,
        ref_id: str,
        title: str,
        *,
        persona: str = "artist",
        child_titles: Optional[List[str]] = None,
        subtitle: Optional[str] = None,
        image_key: Optional[str] = None,
    ) -> str:
        """Persona ref with the multi-item overview shape: drilling
        yields a single ``Play <persona>`` gateway followed by sibling
        containers (albums for artists, works for composers). Drilling
        the gateway yields the persona's terminal action_list
        ``{Shuffle, Start Radio}``.

        When ``child_titles`` is empty or ``None``, the drill collapses
        to the single-item gateway shape (an artist/composer the user
        has no reachable content for — equivalent to ``register_artist``
        / ``register_composer``).

        Cross-checks the persona kind against the gateway title so
        tests can't mis-register a composer as `Play Artist`.
        """
        if persona not in ("artist", "composer"):
            raise ValueError(
                f"persona must be 'artist' or 'composer', got {persona!r}",
            )
        gateway_title = "Play Artist" if persona == "artist" else "Play Composer"
        child_titles = child_titles or []

        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        item_key = f"item-{bare}"
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=ItemIdentity(title=title, hint="list"),
            recipe=SearchRecipe(search_string=title),
            cached_item_key=item_key,
            roon_session_key=self._session_key,
            item_key_path=[],
        )

        gateway_key = f"gateway-{gateway_title}-{bare}"
        items = [
            RoonCoreItemSchema(
                title=gateway_title, item_key=gateway_key, hint="action_list",
            ),
        ]
        for i, child_title in enumerate(child_titles):
            child_key = f"persona-child-{bare}-{i}"
            items.append(
                RoonCoreItemSchema(
                    title=child_title,
                    subtitle=title,
                    item_key=child_key,
                    hint="list",
                ),
            )

        self._drill_responses[item_key] = RoonCoreResultsSchema(
            items=items,
            list=RoonCoreListSchema(
                count=len(items),
                hint=None,
                title=title,
                subtitle=subtitle,
                image_key=image_key,
            ),
        )
        self._drill_responses[gateway_key] = self._make_action_list(
            ["Shuffle", "Start Radio"],
            list_title=title, ref_id=bare,
        )
        return bare

    def _register_track_container(
        self,
        ref_id: str,
        title: str,
        child_titles: List[str],
        gateway_title: str,
        subtitle: Optional[str] = None,
        image_key: Optional[str] = None,
    ) -> str:
        """Shared implementation for album / playlist / work registration.
        Drill yields ``[Play <category>, *leaf_children]`` where each
        child is an ``hint="action_list"`` leaf (drilling a leaf yields
        the standard track action_list)."""
        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        item_key = f"item-{bare}"
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=ItemIdentity(title=title, hint="list"),
            recipe=SearchRecipe(search_string=title),
            cached_item_key=item_key,
            roon_session_key=self._session_key,
            item_key_path=[],
        )

        gateway_key = f"gateway-{gateway_title}-{bare}"
        leaf_items: List[RoonCoreItemSchema] = [
            RoonCoreItemSchema(
                title=gateway_title, item_key=gateway_key, hint="action_list",
            ),
        ]
        for i, child_title in enumerate(child_titles):
            child_key = f"leaf-{bare}-{i}"
            leaf_items.append(
                RoonCoreItemSchema(
                    title=child_title, item_key=child_key, hint="action_list",
                ),
            )
            self._drill_responses[child_key] = self._make_action_list(
                _DEFAULT_ALBUM_ACTIONS,
                list_title=child_title, ref_id=f"{bare}-{i}",
            )

        self._drill_responses[item_key] = RoonCoreResultsSchema(
            items=leaf_items,
            list=RoonCoreListSchema(
                count=len(leaf_items),
                hint=None,
                title=title,
                subtitle=subtitle,
                image_key=image_key,
            ),
        )
        self._drill_responses[gateway_key] = self._make_action_list(
            _DEFAULT_ALBUM_ACTIONS,
            list_title=title, ref_id=bare,
        )
        return bare

    def register_album(
        self,
        ref_id: str,
        title: str,
        track_titles: List[str],
        *,
        subtitle: Optional[str] = None,
        image_key: Optional[str] = None,
    ) -> str:
        """Album drill shape: ``[Play Album, track1..trackN]``. Tracks
        are leaf action_list items; drilling Play Album yields the
        container's action_list (Play Now / Add Next / Queue /
        Start Radio)."""
        return self._register_track_container(
            ref_id, title, track_titles, "Play Album",
            subtitle=subtitle, image_key=image_key,
        )

    def register_playlist(
        self,
        ref_id: str,
        title: str,
        track_titles: List[str],
        *,
        subtitle: Optional[str] = None,
        image_key: Optional[str] = None,
    ) -> str:
        """Playlist drill shape: ``[Play Playlist, track1..trackN]``."""
        return self._register_track_container(
            ref_id, title, track_titles, "Play Playlist",
            subtitle=subtitle, image_key=image_key,
        )

    def register_work(
        self,
        ref_id: str,
        title: str,
        recording_titles: List[str],
        *,
        subtitle: Optional[str] = None,
        image_key: Optional[str] = None,
    ) -> str:
        """Work drill shape: ``[Play Work, recording1..recordingN]``.
        Recordings are leaves — drilling one yields the standard
        action_list."""
        return self._register_track_container(
            ref_id, title, recording_titles, "Play Work",
            subtitle=subtitle, image_key=image_key,
        )

    def register_container_with_duplicate_wrapper(
        self,
        ref_id: str,
        title: str,
        track_titles: List[str],
        *,
        gateway: str = "Play Album",
        subtitle: Optional[str] = None,
        image_key: Optional[str] = None,
        extra_sibling_titles: Optional[List[str]] = None,
    ) -> str:
        """Container reached via a subcategory drill (Albums / Works /
        Playlists): outer drill yields a list whose ``items[0]`` is a
        duplicate of the parent list metadata. Drilling the duplicate
        reaches the standard ``Play <gateway> + leaves`` content.

        When ``extra_sibling_titles`` is set, the outer list contains
        additional same-title items after the duplicate — modelling
        the multi-version case (e.g. Thriller surfacing with 3 versions).
        Same metadata for all of them; the dispatcher's rule is "drill
        the top one, ignore the rest" so the sibling content is never
        needed.
        """
        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        outer_key = f"wrapper-{bare}"
        inner_ref_id = f"{bare}-inner"

        # Identity needs subtitle / image_key matching the wrapper child
        # so the dispatcher's four-field duplicate-level detection
        # recognises the wrapper as a duplicate of the search-result tile.
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=ItemIdentity(
                title=title,
                subtitle=subtitle,
                image_key=image_key,
                hint="list",
            ),
            recipe=SearchRecipe(search_string=title),
            cached_item_key=outer_key,
            roon_session_key=self._session_key,
            item_key_path=[],
        )

        # The duplicate top item — title/subtitle/image_key all match
        # the outer list metadata. Drilling it routes to the inner
        # content via the same item_key used by register_album.
        inner_item_key = f"item-{inner_ref_id}"
        outer_items = [
            RoonCoreItemSchema(
                title=title,
                subtitle=subtitle,
                image_key=image_key,
                item_key=inner_item_key,
                hint="list",
            ),
        ]
        for i, extra_title in enumerate(extra_sibling_titles or []):
            outer_items.append(
                RoonCoreItemSchema(
                    title=extra_title,
                    subtitle=subtitle,
                    image_key=image_key,
                    item_key=f"sibling-{bare}-{i}",
                    hint="list",
                ),
            )
        self._drill_responses[outer_key] = RoonCoreResultsSchema(
            items=outer_items,
            list=RoonCoreListSchema(
                count=len(outer_items),
                hint=None,
                title=title,
                subtitle=subtitle,
                image_key=image_key,
            ),
        )

        # Register the inner container under the inner_item_key. We
        # don't want a second StableReference (the test refers to the
        # outer ref), so we bypass the public helper and just install
        # the drill response directly.
        gateway_key = f"gateway-{gateway}-{inner_ref_id}"
        leaf_items: List[RoonCoreItemSchema] = [
            RoonCoreItemSchema(
                title=gateway, item_key=gateway_key, hint="action_list",
            ),
        ]
        for i, child_title in enumerate(track_titles):
            child_key = f"leaf-{inner_ref_id}-{i}"
            leaf_items.append(
                RoonCoreItemSchema(
                    title=child_title, item_key=child_key, hint="action_list",
                ),
            )
            self._drill_responses[child_key] = self._make_action_list(
                _DEFAULT_ALBUM_ACTIONS,
                list_title=child_title, ref_id=f"{inner_ref_id}-{i}",
            )
        self._drill_responses[inner_item_key] = RoonCoreResultsSchema(
            items=leaf_items,
            list=RoonCoreListSchema(
                count=len(leaf_items),
                hint=None,
                title=title,
                subtitle=subtitle,
                image_key=image_key,
            ),
        )
        self._drill_responses[gateway_key] = self._make_action_list(
            _DEFAULT_ALBUM_ACTIONS,
            list_title=title, ref_id=inner_ref_id,
        )
        return bare

    def register_not_found_container(
        self,
        ref_id: str,
        title: str,
        *,
        subtitle: Optional[str] = None,
    ) -> str:
        """Container ref whose drill yields a single
        ``(title="Not Found", subtitle=null, image_key=null, hint=null)``
        child — Roon's signal that the indexed item isn't reachable
        (typical for works that point at unavailable streaming
        recordings). The dispatcher should filter the "Not Found"
        entry and emit a notice rather than treating it as content."""
        bare = ref_id[2:] if ref_id.startswith("S:") else ref_id
        item_key = f"item-{bare}"
        self.session_manager.refs[bare] = StableReference(
            ref_id=bare,
            identity=ItemIdentity(title=title, hint="list"),
            recipe=SearchRecipe(search_string=title),
            cached_item_key=item_key,
            roon_session_key=self._session_key,
            item_key_path=[],
        )
        self._drill_responses[item_key] = RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(
                    title="Not Found",
                    subtitle=None,
                    image_key=None,
                    item_key=f"notfound-{bare}",
                    hint=None,
                ),
            ],
            list=RoonCoreListSchema(
                count=1,
                hint=None,
                title=title,
                subtitle=subtitle,
            ),
        )
        return bare

    def register_category_search_chain(
        self,
        track_ref_id: str,
        title: str,
        intended_category: str,
        album_track_titles: List[str],
    ) -> str:
        """Set up the multi-step chain that
        ``RoonBrowseMixin._correct_via_category_search`` performs.

        Models the scenario: a search reference resolves to a track,
        but the caller intended an album (or playlist/composer/work).
        Production reconcile triggers the chain: re-search → drill the
        category (e.g. "Albums") → match the title → drill the matched
        item to its container level → return those results.

        After this helper, ``_expand_container_reference`` will see the corrected
        results (a container with ``album_track_titles`` as children)
        and enumerate them as track refs.
        """
        bare = track_ref_id[2:] if track_ref_id.startswith("S:") else track_ref_id
        self.register_track(bare, title)

        category_name = {
            "album": "Albums", "playlist": "Playlists",
            "composer": "Composers", "work": "Works",
        }[intended_category]

        category_key = f"category-{category_name}-{bare}"
        matched_key = f"match-{intended_category}-{bare}"

        # The recipe's search_string was set to *title* by register_track,
        # so the re-search input is *title*.
        self._search_responses[title] = [
            RoonCoreItemSchema(
                title=category_name, item_key=category_key, hint="list",
            ),
        ]

        # Drilling the category → list including the matched title.
        self._drill_responses[category_key] = RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(
                    title=title, item_key=matched_key, hint="list",
                ),
            ],
            list=RoonCoreListSchema(
                count=1, hint="list", title=category_name,
            ),
        )

        # Drilling the matched album → container with [Play Album,
        # track1, ..., trackN]. ``_expand_container_reference`` skips gateway
        # titles ("Play Album"/"Play Playlist") so they don't pollute
        # the expanded list.
        gateway_title = {
            "album": "Play Album", "playlist": "Play Playlist",
            "composer": "Play Composer", "work": "Play Work",
        }[intended_category]
        track_items = []
        for i, t in enumerate(album_track_titles):
            track_key = f"track-{bare}-{i}"
            track_items.append(
                RoonCoreItemSchema(
                    title=t, item_key=track_key, hint="action_list",
                ),
            )
            self._drill_responses[track_key] = self._make_action_list(
                _DEFAULT_ALBUM_ACTIONS,
                list_title=t, ref_id=f"{bare}-{i}",
            )
        self._drill_responses[matched_key] = RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(
                    title=gateway_title,
                    item_key=f"gw-{bare}",
                    hint="Action",
                ),
                *track_items,
            ],
            list=RoonCoreListSchema(
                count=len(track_items) + 1, hint="list", title=title,
            ),
        )
        return bare

    def add_search_response(
        self, query: str, ref_ids: List[str],
    ) -> None:
        """Register what a search for *query* returns. Each ref_id must
        already have been registered via ``register_item``."""
        items: List[RoonCoreItemSchema] = []
        for rid in ref_ids:
            bare = rid[2:] if rid.startswith("S:") else rid
            ref = self.session_manager.refs.get(bare)
            if ref is None:
                raise ValueError(
                    f"add_search_response: ref_id {rid!r} not registered",
                )
            items.append(
                RoonCoreItemSchema(
                    title=ref.identity.title,
                    item_key=ref.cached_item_key or f"item:{bare}",
                    hint=ref.identity.hint,
                ),
            )
        self._search_responses[query] = items

    def _make_action_list(
        self,
        titles: List[str], *, list_title: str, ref_id: str,
    ) -> RoonCoreResultsSchema:
        items = []
        for t in titles:
            key = f"action:{t.lower().replace(' ', '-')}:{ref_id}"
            self._action_lookup[key] = (t, ref_id)
            items.append(
                RoonCoreItemSchema(title=t, item_key=key, hint="Action"),
            )
        return RoonCoreResultsSchema(
            items=items,
            list=RoonCoreListSchema(
                count=len(titles), hint="action_list", title=list_title,
            ),
        )

    # ------------------------------------------------------------------
    # browse_core — the API boundary
    # ------------------------------------------------------------------

    def browse_core(
        self,
        aux: dict,
        zone: Optional[str] = None,
        session_key: Optional[str] = None,
        update_current: bool = True,
    ) -> RoonCoreResultsSchema:
        _ = (session_key, update_current)
        self.browse_aux_calls.append(dict(aux))
        self.browse_zones.append(zone)

        # Search call: pop_all + input
        if aux.get("pop_all") and "input" in aux:
            items = self._search_responses.get(aux["input"], [])
            return RoonCoreResultsSchema(
                items=list(items),
                list=RoonCoreListSchema(
                    count=len(items),
                    hint=None,
                    title=aux["input"],
                ),
            )

        item_key = aux.get("item_key")
        if item_key is None:
            return self._empty()

        # Drill call. If it's a registered drill target, return the
        # response. Otherwise treat it as an action-execution call
        # (Roon auto-pops; we just record it and return empty).
        response = self._drill_responses.get(item_key)
        if response is not None:
            return response

        # Action execution
        self.action_dispatches.append({
            "item_key": item_key,
            "zone": zone,
            "session_key": session_key,
        })
        return self._empty()

    @staticmethod
    def _empty() -> RoonCoreResultsSchema:
        return RoonCoreResultsSchema(
            items=[],
            list=RoonCoreListSchema(count=0, hint="action_list"),
        )

    # ------------------------------------------------------------------
    # Nav primitives — delegate to browse_core for simulation symmetry
    # ------------------------------------------------------------------

    def _nav_drill(
        self,
        item_key: str,
        session_key: str,
        zone: Optional[str] = None,
        update_current: bool = True,
    ) -> RoonCoreResultsSchema:
        return self.browse_core(
            aux={"item_key": item_key},
            zone=zone,
            session_key=session_key,
            update_current=update_current,
        )

    def _nav_reset_to_root(
        self, session_key: str, zone: Optional[str] = None,
    ) -> None:
        _ = zone
        self.session_manager.set_session_depth(session_key, 0)

    def _lookup_output_id(self, zone_name: Optional[str] = None) -> str:
        """Cross-mixin dep: lives on RoonZoneMixin in production. Stub
        with a placeholder — production browse_opts only use this as
        an opaque address that flows into the Roon API request, and we
        never make that request (browse_core is faked)."""
        _ = zone_name
        return "fake-output"

    # ------------------------------------------------------------------
    # Zone / transport recorders — used by tests that drive the action
    # tool but don't care about the actual transport behaviour.
    # ------------------------------------------------------------------

    def get_zone_snapshot(self, zone: Optional[str] = None) -> dict:
        return {
            "display_name": zone or "Living Room",
            "state": self.zone_state,
            "zone_id": "z1",
        }

    def get_zone_names(self) -> List[str]:
        names = [
            z["display_name"]
            for z in self.api.zones.values()
            if isinstance(z, dict) and "display_name" in z
        ]
        return names or ["Living Room"]

    def playback_control(
        self, control: str, zone: Optional[str] = None,
    ) -> None:
        self.playback_calls.append({"control": control, "zone": zone})

    def set_auto_radio(
        self, auto_radio: bool, zone: Optional[str] = None,
    ) -> None:
        self.set_auto_radio_calls.append(
            {"auto_radio": auto_radio, "zone": zone},
        )

    def set_shuffle(self, shuffle: bool, zone: Optional[str] = None) -> None:
        _ = zone
        self.shuffle_calls.append(shuffle)

    def set_repeat(self, repeat: str, zone: Optional[str] = None) -> None:
        _ = zone
        self.repeat_calls.append(repeat)

    def seek(
        self,
        seconds: int,
        method: str = "absolute",
        zone: Optional[str] = None,
    ) -> None:
        _ = zone
        self.seek_calls.append({"seconds": seconds, "method": method})

    def get_volume_percent(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> int:
        _ = (zone, output)
        self.volume_get_calls += 1
        return 55

    def set_volume_percent(
        self,
        volume: int,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        _ = (zone, output)
        self.set_volume_calls.append(volume)

    def change_volume_percent(
        self,
        delta: int,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        _ = (zone, output)
        self.change_volume_calls.append(delta)

    def mute(
        self,
        mute: bool,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        _ = (zone, output)
        self.mute_calls.append(mute)

    def pause_all(self) -> None:
        self.pause_all_calls += 1

    def standby(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        _ = (zone, output)
        self.standby_calls += 1

    def convenience_switch(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        _ = (zone, output)
        self.convenience_switch_calls += 1

    # ------------------------------------------------------------------
    # Convenience accessors for tests
    # ------------------------------------------------------------------

    @property
    def dispatched_actions(self) -> List[tuple[str, str]]:
        """Sequence of ``(action_title, ref_id)`` pairs for every action
        the tool dispatched, in dispatch order. Used by tests that need
        to assert on per-item action sequencing without coupling to the
        synthetic item_key format."""
        return [
            self._action_lookup[d["item_key"]]
            for d in self.action_dispatches
            if d["item_key"] in self._action_lookup
        ]

    @property
    def get_media_actions_calls(self) -> List[str]:
        """Back-compat shim: tests assert on the references that were
        passed to get_media_actions. Reconstruct from the drill calls
        (every get_media_actions call drills cached_item_key first)."""
        # Map item_key → ref_id by walking session_manager.refs.
        key_to_ref: Dict[str, str] = {}
        for ref in self.session_manager.refs.values():
            if ref.cached_item_key:
                key_to_ref[ref.cached_item_key] = f"S:{ref.ref_id}"
        seen: List[str] = []
        for aux in self.browse_aux_calls:
            ref = key_to_ref.get(aux.get("item_key", ""))
            if ref and ref not in seen:
                seen.append(ref)
        return seen

    @property
    def browse_calls(self) -> List[str]:
        """Back-compat shim: list of item_keys that browse_core saw."""
        return [
            aux.get("item_key", "") for aux in self.browse_aux_calls
            if "item_key" in aux
        ]


def make_action_tool(
    fake: BrowseFake,
    *,
    result_store: Optional[Dict[str, Any]] = None,
):
    """Build a RoonActionTool wired to *fake* with a passthrough zone
    resolver and the given result_store.

    Imported lazily inside the function so the module can be imported
    by tests that don't need the action tool (e.g. browse-only tests).
    """
    from tools.roon_action import RoonActionTool, RoonActionToolConfig

    tool = RoonActionTool(config=RoonActionToolConfig(
        resolve_zone=lambda z: z,
        result_store=result_store if result_store is not None else {},
    ))
    tool.roon_connection = fake
    return tool
