"""Tests for complex shuffle logic: _expand_container_reference, count parameter,
three shuffle paths, and category reconciliation in expansion.

These run production code (``RoonBrowseMixin``) on the call path via
``BrowseFake``: production resolves references, drills into containers,
mints child refs, and triggers reconciliation. The fake stubs only
``browse_core`` (the API boundary) plus the zone/transport recorders.
"""

import asyncio
import unittest
from typing import List

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.browse_session import (  # noqa: E402
    ItemIdentity,
    SearchRecipe,
    StableReference,
)
from roon_core.schemas import (  # noqa: E402
    RoonCoreItemSchema,
    RoonCoreItemSummarySchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)

try:
    from tests._browse_fake import BrowseFake, make_action_tool
except ModuleNotFoundError:
    from _browse_fake import BrowseFake, make_action_tool

from tools.roon_action import (  # noqa: E402
    RoonActionTool,
    RoonActionToolInputSchema,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _item(reference: str, title: str = "Track") -> RoonCoreItemSummarySchema:
    return RoonCoreItemSummarySchema(title=title, reference=reference)


def _track_titles(count: int, prefix: str = "Track") -> List[str]:
    return [f"{prefix} {i + 1}" for i in range(count)]


def _make_tool(fake: BrowseFake) -> RoonActionTool:
    return make_action_tool(fake)


# ══════════════════════════════════════════════════════════════════════
# Schema tests
# ══════════════════════════════════════════════════════════════════════


class TestCountParameter(unittest.TestCase):

    def test_count_accepted_for_shuffle(self):
        schema = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "Track 1")],
            count=5,
        )
        self.assertEqual(schema.count, 5)

    def test_count_defaults_to_none(self):
        schema = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "Track 1")],
        )
        self.assertIsNone(schema.count)

# ══════════════════════════════════════════════════════════════════════
# _expand_container_reference tests
# ══════════════════════════════════════════════════════════════════════


class TestExpandReference(unittest.TestCase):

    def test_track_returns_single_item(self):
        """A track (action_list hint) expands to just itself."""
        fake = BrowseFake()
        fake.register_track("aaa01", "A Track")
        tool = _make_tool(fake)

        result = tool._expand_container_reference(_item("S:aaa01", "A Track"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "A Track")
        self.assertEqual(result[0].reference, "S:aaa01")

    def test_album_expands_to_tracks(self):
        """An album expands to its individual tracks with minted S: refs."""
        fake = BrowseFake()
        fake.register_container(
            "aaa02", "My Album", _track_titles(8, "Song"),
        )
        tool = _make_tool(fake)

        result = tool._expand_container_reference(_item("S:aaa02", "My Album"))
        self.assertEqual(len(result), 8)
        for item in result:
            self.assertTrue(item.reference.startswith("S:"))
            self.assertTrue(item.title.startswith("Song "))

    def test_expanded_refs_are_unique(self):
        """Each expanded track gets a unique reference."""
        fake = BrowseFake()
        fake.register_container("ccc01", "Album", _track_titles(10))
        tool = _make_tool(fake)

        result = tool._expand_container_reference(_item("S:ccc01", "Album"))
        refs = [item.reference for item in result]
        self.assertEqual(len(refs), len(set(refs)), "References should be unique")

    def test_expanded_children_pinned_as_track_category(self):
        """Children from album/playlist expansion must be marked as
        intended_category='track'. Otherwise the action execution step
        inherits the request's intended_item_category (e.g. 'album') and
        category reconciliation can re-promote a legitimate track back to
        an album, queueing the whole album.
        """
        fake = BrowseFake()
        fake.register_container(
            "ddd01", "Multi-track Album", _track_titles(5),
        )
        tool = _make_tool(fake)

        result = tool._expand_container_reference(
            _item("S:ddd01", "Multi-track Album"),
            intended_category="album",
        )
        self.assertEqual(len(result), 5)
        for item in result:
            self.assertEqual(
                item.intended_category, "track",
                f"Expected intended_category='track' on {item.title}; "
                f"got {item.intended_category!r}",
            )

    def test_disambiguation_drills_through_first_version(self):
        """An album with multiple versions drills through the first one."""
        fake = BrowseFake()
        fake.register_album_with_versions(
            "ddd01", "Thriller",
            [_track_titles(9, "Thriller Track") for _ in range(3)],
        )
        tool = _make_tool(fake)

        result = tool._expand_container_reference(_item("S:ddd01", "Thriller"))
        self.assertEqual(
            len(result), 9,
            "Should expand through first version to get 9 tracks",
        )
        for item in result:
            self.assertTrue(item.title.startswith("Thriller Track"))

    def test_disambiguation_single_version(self):
        """A single 'list'-shaped wrapper child drills through to its
        track listing — handled by the wrapper-strip branch."""
        fake = BrowseFake()
        fake.register_album_with_versions(
            "eee01", "Some Album",
            [_track_titles(5)],
        )
        tool = _make_tool(fake)

        result = tool._expand_container_reference(_item("S:eee01", "Some Album"))
        self.assertEqual(len(result), 5)

    def test_self_loop_wrapper_returns_original_unexpanded(self):
        """When Roon returns a 1-item duplicate wrapper that points back
        to itself (drilling key X returns an item whose key is also X),
        we must stop drilling and return the original item un-expanded.
        Otherwise the code falls through to the container branch and
        mints the wrapper itself as a bogus 'track' reference — and the
        redundant drill wedges the browse session's depth tracking.
        """
        fake = BrowseFake()
        # Insert a ref with cached_item_key "471:9" directly so we
        # control the keys precisely (the self-loop happens when the
        # wrapper's key equals the result of drilling it).
        fake.session_manager.refs["fff01"] = StableReference(
            ref_id="fff01",
            identity=ItemIdentity(
                title="Final Cut (2011 Remastered Version)", hint="list",
            ),
            recipe=SearchRecipe(
                search_string="Final Cut (2011 Remastered Version)",
            ),
            cached_item_key="471:9",
            roon_session_key=fake._session_key,
            item_key_path=[],
        )
        wrapper = RoonCoreItemSchema(
            title="Final Cut (2011 Remastered Version)",
            item_key="500:0",
            hint="list",
        )
        # Drilling "471:9" returns the wrapper at "500:0".
        fake.register_drill("471:9", RoonCoreResultsSchema(
            items=[wrapper],
            list=RoonCoreListSchema(count=1, hint="list"),
        ))
        # Drilling "500:0" returns the same wrapper (self-loop).
        fake.register_drill("500:0", RoonCoreResultsSchema(
            items=[wrapper],
            list=RoonCoreListSchema(count=1, hint="list"),
        ))
        tool = _make_tool(fake)

        result = tool._expand_container_reference(
            _item("S:fff01", "Final Cut (2011 Remastered Version)"),
        )

        # Must return the original item un-expanded — NOT a minted track ref
        # pointing at the wrapper itself.
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].reference, "S:fff01",
            "self-loop wrapper must fall back to the original album ref, "
            f"not mint a bogus track ref ({result[0].reference})",
        )
        # Drilling stops once the self-loop is detected — exactly two
        # browse_core drills (album, wrapper).
        self.assertEqual(
            fake.browse_calls, ["471:9", "500:0"],
            f"expected exactly two drills (album, wrapper); got {fake.browse_calls}",
        )


# ══════════════════════════════════════════════════════════════════════
# Shuffle path tests
# ══════════════════════════════════════════════════════════════════════


class TestShuffleSingleItem(unittest.TestCase):
    """Shuffle on one input item — always expands to tracks, randomises,
    plays the first and queues the rest. Real Roon's album/track action
    menu has no native ``Shuffle`` entry, so randomisation is done by
    expanding the item to its track list before dispatch.
    """

    def test_single_track_plays_just_the_track(self):
        """A track expands to itself; shuffling a one-item list
        changes nothing so the track gets a single Play Now dispatch."""
        fake = BrowseFake()
        fake.register_track("aaa01", "Lone Track")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "Lone Track")],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Play Now", "aaa01")])

    def test_single_album_no_count_plays_all_tracks_shuffled(self):
        """An album expands to its track list; all tracks get dispatched
        (Play Now seeds the first, Queue adds the rest)."""
        fake = BrowseFake()
        fake.register_container("aaa01", "Whole Album", _track_titles(7, "Song"))
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "Whole Album")],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        actions = fake.dispatched_actions
        self.assertEqual(
            len(actions), 7,
            f"Expected 7 dispatches (one per track), got {actions}",
        )
        self.assertEqual(actions[0][0], "Play Now")
        for action_title, _ref in actions[1:]:
            self.assertEqual(action_title, "Queue")
        dispatched_refs = sorted(ref for _, ref in actions)
        self.assertEqual(
            len(set(dispatched_refs)), len(dispatched_refs),
            f"Every dispatched ref must be distinct, got {dispatched_refs}",
        )


class TestShufflePath2SingleItemWithCount(unittest.TestCase):
    """Single item + count → expand, shuffle, truncate, play."""

    def test_single_album_with_count_plays_exact_count(self):
        fake = BrowseFake()
        fake.register_container("aaa01", "Big Album", _track_titles(20, "Song"))
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "Big Album")],
            count=5,
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        actions = fake.dispatched_actions
        self.assertEqual(
            len(actions), 5,
            f"Expected 5 dispatches, got {actions}",
        )
        self.assertEqual(actions[0][0], "Play Now")
        for action_title, _ref in actions[1:]:
            self.assertEqual(action_title, "Queue")

    def test_count_larger_than_track_list_plays_all(self):
        fake = BrowseFake()
        fake.register_container(
            "aaa02", "Short Album",
            _track_titles(3, "Short Album Track"),
        )
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa02", "Short Album")],
            count=100,
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(
            len(fake.dispatched_actions), 3,
            "Should play all 3 tracks when count > total",
        )


class TestShufflePath3MultipleItems(unittest.TestCase):
    """Multiple items → expand all, shuffle, optional truncate."""

    def test_multiple_tracks_shuffled(self):
        """Multiple individual tracks are shuffled without expansion."""
        fake = BrowseFake()
        for ref, title in [
            ("aaa01", "Track A"), ("bbb01", "Track B"), ("ccc01", "Track C"),
        ]:
            fake.register_track(ref, title)
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                _item("S:aaa01", "Track A"),
                _item("S:bbb01", "Track B"),
                _item("S:ccc01", "Track C"),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(len(fake.dispatched_actions), 3)

    def test_album_plus_track_expands_album(self):
        """An album + a track: album expands, track stays, all shuffled together."""
        fake = BrowseFake()
        fake.register_container("aaa01", "My Album", _track_titles(5, "Album Track"))
        fake.register_track("bbb01", "Bonus Track")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                _item("S:aaa01", "My Album"),
                _item("S:bbb01", "Bonus Track"),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        # 5 album tracks + 1 standalone = 6 total
        self.assertEqual(
            len(fake.dispatched_actions), 6,
            f"Expected 6 dispatches, got {fake.dispatched_actions}",
        )

    def test_multiple_items_with_count_truncates(self):
        """Multiple items with count: expand all, shuffle, take count."""
        fake = BrowseFake()
        fake.register_container("aaa01", "Big Album", _track_titles(10, "Album Track"))
        fake.register_track("bbb01", "Extra Track")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                _item("S:aaa01", "Big Album"),
                _item("S:bbb01", "Extra Track"),
            ],
            count=3,
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(len(fake.dispatched_actions), 3)


class TestCountIgnoredForOtherActions(unittest.TestCase):
    """count parameter has no effect on non-Shuffle actions."""

    def test_play_now_ignores_count(self):
        fake = BrowseFake()
        fake.register_track("aaa01", "Track A")
        fake.register_track("bbb01", "Track B")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Play Now",
            items=[_item("S:aaa01", "Track A"), _item("S:bbb01", "Track B")],
            count=1,
        )
        asyncio.run(tool.run_async(params))

        # Both items dispatched — count ignored
        self.assertEqual(len(fake.dispatched_actions), 2)


# ══════════════════════════════════════════════════════════════════════
# Category reconciliation in _expand_container_reference
# ══════════════════════════════════════════════════════════════════════


class TestExpandReferenceWithCategoryReconciliation(unittest.TestCase):
    """``_expand_container_reference`` with intended_category triggers production
    reconciliation. Tests use ``BrowseFake.register_category_search_chain``
    to wire the multi-step browse responses that
    ``_correct_via_category_search`` walks."""

    def test_track_ref_intended_album_expands_album(self):
        """Track ref + intended_category='album' → reconcile triggers
        category search → drills the matched album → expands tracks."""
        fake = BrowseFake()
        fake.register_category_search_chain(
            "aaa01", "Thriller",
            intended_category="album",
            album_track_titles=_track_titles(9, "Thriller Track"),
        )
        tool = _make_tool(fake)

        result = tool._expand_container_reference(
            _item("S:aaa01", "Thriller"),
            intended_category="album",
        )
        self.assertEqual(
            len(result), 9,
            f"Expected 9 album tracks, got {len(result)}",
        )
        for item in result:
            self.assertTrue(item.title.startswith("Thriller Track"))
            self.assertTrue(item.reference.startswith("S:"))

    def test_auto_category_no_reconciliation(self):
        """intended_category='auto' should not trigger reconciliation."""
        fake = BrowseFake()
        fake.register_track("ccc01", "A Track")
        tool = _make_tool(fake)

        result = tool._expand_container_reference(
            _item("S:ccc01", "A Track"),
            intended_category="auto",
        )
        self.assertEqual(len(result), 1)

    def test_track_ref_intended_album_raises_when_no_albums_category(self):
        """Track ref + ``intended_category='album'`` where the re-search
        returns no Albums category: production reconcile triggers
        ``_correct_via_category_search``, which raises
        ``CategoryCorrectionFailed`` (failure_mode='no_category').
        ``_expand_container_reference`` catches that and re-raises as ``ValueError``
        so the per-item dispatcher routes it through the structured
        error bucket.

        This pins the loud-failure contract — the corrector must not
        silently fall back to the original (wrong-category) ref, which
        would play wrong content.
        """
        fake = BrowseFake()
        # Track ref with an action_list drill (track shape).
        fake.register_track("ddd01", "Thriller")
        # The re-search response has NO "Albums" category — production
        # reconcile's _correct_via_category_search raises
        # CategoryCorrectionFailed(failure_mode='no_category').
        fake._search_responses["Thriller"] = [
            RoonCoreItemSchema(
                title="Tracks", item_key="cat-tracks", hint="list",
            ),
        ]
        tool = _make_tool(fake)

        with self.assertRaises(ValueError) as ctx:
            tool._expand_container_reference(
                _item("S:ddd01", "Thriller"),
                intended_category="album",
            )
        # The error message names the intended category so the
        # coordinator can act on it.
        self.assertIn("Albums", str(ctx.exception))

    def test_album_ref_intended_track_resolves_via_gateway_sibling(self):
        """Album ref + intended_category='track' where the ref's
        identity title matches a sibling track at the album's gateway
        level: production reconcile fires
        ``_correct_via_gateway_siblings``, drills the matching track,
        and ``_expand_container_reference`` returns it as a single item tagged
        ``intended_category='track'``.

        This is the album→track direction of category reconciliation,
        complementing the track→album coverage in
        ``test_track_ref_intended_album_expands_album``.
        """
        fake = BrowseFake()
        # The container's drill yields the gateway level
        # ``[Play Album, Other Track, Specific Track, Yet Another]``,
        # and the ref's identity title ("Specific Track") matches one
        # of the siblings.
        fake.register_container(
            "bbb01", "Specific Track",
            ["Other Track", "Specific Track", "Yet Another"],
            include_play_album_gateway=True,
        )
        # The matching sibling's drill response (its action_list) — the
        # gateway-sibling correction drills into the matched item.
        fake.register_drill(
            "track-key-bbb01-1",  # second child after gateway, index 1
            RoonCoreResultsSchema(
                items=[
                    RoonCoreItemSchema(
                        title=t,
                        item_key=f"action-{t}-bbb01",
                        hint="Action",
                    )
                    for t in ["Play Now", "Add Next", "Queue", "Start Radio"]
                ],
                list=RoonCoreListSchema(
                    count=4, hint="action_list", title="Specific Track",
                ),
            ),
        )
        tool = _make_tool(fake)

        result = tool._expand_container_reference(
            _item("S:bbb01", "Specific Track"),
            intended_category="track",
        )

        self.assertEqual(
            len(result), 1,
            f"Expected gateway-sibling correction to resolve to a "
            f"single track; got {len(result)} items: "
            f"{[r.title for r in result]}",
        )
        self.assertEqual(result[0].title, "Specific Track")
        self.assertEqual(result[0].intended_category, "track")


class TestShuffleWithMixedCategories(unittest.TestCase):
    """Shuffle with per-item intended_category triggers reconciliation
    during expansion."""

    def test_mixed_shuffle_corrects_track_to_album(self):
        """Shuffle with intended_category='album' on a track ref should
        expand the album via the production category-search chain."""
        fake = BrowseFake()
        fake.register_category_search_chain(
            "aaa01", "Thriller",
            intended_category="album",
            album_track_titles=_track_titles(5, "Thriller Track"),
        )
        fake.register_track("bbb01", "Down Under")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                RoonCoreItemSummarySchema(
                    title="Thriller", reference="S:aaa01",
                    intended_category="album",
                ),
                RoonCoreItemSummarySchema(
                    title="Down Under", reference="S:bbb01",
                    intended_category="track",
                ),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        # 5 album tracks + 1 standalone = 6 total
        self.assertEqual(
            len(fake.dispatched_actions), 6,
            f"Expected 6 dispatches (5 album + 1 track), got "
            f"{fake.dispatched_actions}",
        )


# ══════════════════════════════════════════════════════════════════════
# reconcile_intended_category — production logic, focused stubs on the
# correction sub-methods.
# ══════════════════════════════════════════════════════════════════════


def _make_ref(title: str = "Thriller", hint: str = "action_list",
              search_string: str = "Thriller Michael Jackson"):
    """Create a StableReference for testing reconciliation."""
    return StableReference(
        ref_id="test-ref",
        identity=ItemIdentity(title=title, hint=hint),
        recipe=SearchRecipe(search_string=search_string),
        cached_item_key="test-key",
        roon_session_key="test-session",
        item_key_path=["test-key"],
    )


class TestReconcileIntendedCategory(unittest.TestCase):
    """Tests the real ``RoonBrowseMixin.reconcile_intended_category``
    decision tree end-to-end against a ``BrowseFake`` that stubs only
    ``browse_core`` (the Roon API boundary). The real
    ``_correct_via_gateway_siblings`` and ``_correct_via_category_search``
    sub-methods run; we assert on the observable browse_core calls those
    sub-methods make (search vs sibling drill vs nothing) to verify
    dispatch.

    The sub-methods' deeper behaviour (search-chain mechanics,
    no-category-raise, etc.) is covered in test_category_correction.py
    and the BrowseFake-driven integration tests above
    (TestExpandReferenceWithCategoryReconciliation).
    """

    def setUp(self):
        self.fake = BrowseFake()

    def _search_calls(self):
        """Browse_core calls that are searches (pop_all + input)."""
        return [c for c in self.fake.browse_aux_calls if c.get("pop_all")]

    def _drill_calls(self):
        """Browse_core calls that are drills (item_key, no pop_all)."""
        return [
            c for c in self.fake.browse_aux_calls
            if "item_key" in c and not c.get("pop_all")
        ]

    def test_auto_returns_none(self):
        """auto category never triggers correction."""
        result = self.fake.reconcile_intended_category(
            _make_ref(), "auto",
            RoonCoreResultsSchema(items=[], list=RoonCoreListSchema(count=0, hint="action_list")),
            self.fake._session_key,
        )
        self.assertIsNone(result)
        self.assertEqual(self.fake.browse_aux_calls, [])

    def test_action_list_mismatch_triggers_category_search(self):
        """Direct action_list (track) with intended=album triggers
        _correct_via_category_search — observable as a search-style
        browse_core call with the ref's search string. (The search
        itself will fail with CategoryCorrectionFailed because our fake
        returns no Albums category — that's covered separately in
        test_category_correction.py; here we only verify the dispatch.)"""
        from app.exceptions import CategoryCorrectionFailed
        results = RoonCoreResultsSchema(
            items=[RoonCoreItemSchema(title="Play Now", hint="action")],
            list=RoonCoreListSchema(count=1, hint="action_list", title="Thriller"),
        )
        with self.assertRaises(CategoryCorrectionFailed):
            self.fake.reconcile_intended_category(
                _make_ref(), "album", results, self.fake._session_key,
            )
        searches = self._search_calls()
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]["input"], "Thriller Michael Jackson")
        # Gateway-sibling path NOT taken (no drill on a sibling item_key).
        self.assertEqual(self._drill_calls(), [])

    def test_single_action_list_child_wrapper_triggers_category_search(self):
        """Wrapper level (list_hint=null, single child hint=action_list)
        with intended=album triggers category search. The Thriller-bug
        regression — wrapper around a single track must be treated as a
        track and re-searched."""
        from app.exceptions import CategoryCorrectionFailed
        results = RoonCoreResultsSchema(
            items=[RoonCoreItemSchema(title="Thriller", item_key="2945:0", hint="action_list")],
            list=RoonCoreListSchema(count=1, hint=None, title="Thriller"),
        )
        with self.assertRaises(CategoryCorrectionFailed):
            self.fake.reconcile_intended_category(
                _make_ref(), "album", results, self.fake._session_key,
            )
        searches = self._search_calls()
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]["input"], "Thriller Michael Jackson")

    def test_gateway_mismatch_triggers_gateway_siblings(self):
        """Play Album gateway with intended=track triggers
        _correct_via_gateway_siblings — observable as a drill (no search)
        on the matched sibling's item_key."""
        results = RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Play Album", item_key="gateway-key", hint="action_list"),
                RoonCoreItemSchema(title="Thriller", item_key="track-key", hint="action_list"),
            ],
            list=RoonCoreListSchema(count=2, hint=None),
        )
        self.fake.reconcile_intended_category(
            _make_ref(), "track", results, self.fake._session_key,
        )
        # Sibling drill on the matched track key; no search call.
        drills = self._drill_calls()
        self.assertEqual(len(drills), 1)
        self.assertEqual(drills[0]["item_key"], "track-key")
        self.assertEqual(self._search_calls(), [])

    def test_gateway_match_returns_none(self):
        """Play Album gateway with intended=album — no correction needed."""
        results = RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Play Album", item_key="gateway-key", hint="action_list"),
                RoonCoreItemSchema(title="Track 1", item_key="t1", hint="action_list"),
            ],
            list=RoonCoreListSchema(count=2, hint=None),
        )
        result = self.fake.reconcile_intended_category(
            _make_ref(), "album", results, self.fake._session_key,
        )
        self.assertIsNone(result)
        self.assertEqual(self.fake.browse_aux_calls, [])

    def test_action_list_matching_gateway_returns_none(self):
        """Action list titled 'Play Album' with intended=album — already correct."""
        results = RoonCoreResultsSchema(
            items=[RoonCoreItemSchema(title="Play Now", hint="action")],
            list=RoonCoreListSchema(count=1, hint="action_list", title="Play Album"),
        )
        result = self.fake.reconcile_intended_category(
            _make_ref(), "album", results, self.fake._session_key,
        )
        self.assertIsNone(result)
        self.assertEqual(self.fake.browse_aux_calls, [])

    def test_multiple_children_not_treated_as_track_wrapper(self):
        """Multiple children — even if all action_list — are not a track wrapper."""
        results = RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Track 1", hint="action_list"),
                RoonCoreItemSchema(title="Track 2", hint="action_list"),
            ],
            list=RoonCoreListSchema(count=2, hint=None),
        )
        result = self.fake.reconcile_intended_category(
            _make_ref(), "album", results, self.fake._session_key,
        )
        self.assertIsNone(result)
        self.assertEqual(self.fake.browse_aux_calls, [])

    def test_no_search_string_skips_category_search(self):
        """Track wrapper detected but no search_string — short-circuit
        at the dispatch level (production checks
        ``ref.recipe.search_string`` before entering category_search)."""
        results = RoonCoreResultsSchema(
            items=[RoonCoreItemSchema(title="Thriller", hint="action_list")],
            list=RoonCoreListSchema(count=1, hint=None, title="Thriller"),
        )
        ref = _make_ref(search_string="")
        result = self.fake.reconcile_intended_category(
            ref, "album", results, self.fake._session_key,
        )
        self.assertIsNone(result)
        self.assertEqual(self.fake.browse_aux_calls, [])


# ══════════════════════════════════════════════════════════════════════
# Artist rejection in Shuffle Path 2/3, and the Play Artist action
# ══════════════════════════════════════════════════════════════════════


class TestShuffleArtistHandling(unittest.TestCase):
    """Shuffle on a single artist dispatches Roon's native persona
    Shuffle (per the matrix). Multi-artist Shuffle and mixed
    persona+container Shuffle reject the whole call with operator-
    actionable guidance pointing at drill-into-Albums.
    """

    def test_multiple_artists_rejects_with_both_refs(self):
        fake = BrowseFake()
        fake.register_artist("aaa01", "Glenn Miller Orchestra")
        fake.register_artist("bbb01", "Count Basie Orchestra")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                _item("S:aaa01", "Glenn Miller Orchestra"),
                _item("S:bbb01", "Count Basie Orchestra"),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("FAILED", output.result)
        self.assertIsNotNone(output.errors)
        all_refs = set()
        for err in output.errors:
            all_refs.update(err.refs)
        self.assertEqual(all_refs, {"S:aaa01", "S:bbb01"})
        self.assertEqual(fake.dispatched_actions, [])

    def test_single_artist_dispatches_native_shuffle(self):
        """A bare Shuffle on a single artist dispatches Roon's native
        persona Shuffle action — equivalent to Roon's "play this
        artist" + radio."""
        fake = BrowseFake()
        fake.register_artist("aaa01", "The xx")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "The xx")],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "aaa01")])

    def test_single_artist_with_count_dispatches_native_shuffle(self):
        """count is ignored when the matrix routes to native persona
        Shuffle — Roon's radio-style playback doesn't truncate."""
        fake = BrowseFake()
        fake.register_artist("aaa01", "The xx")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[_item("S:aaa01", "The xx")],
            count=10,
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "aaa01")])

    def test_mixed_artist_and_album_rejects_whole_call(self):
        """Shuffle is all-or-nothing — a persona among the items
        rejects the whole call to keep the shuffled pool
        representative."""
        fake = BrowseFake()
        fake.register_container(
            "aaa01", "Some Album", _track_titles(5, "Album Track"),
        )
        fake.register_artist("bbb01", "Some Artist")
        tool = _make_tool(fake)

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                _item("S:aaa01", "Some Album"),
                _item("S:bbb01", "Some Artist"),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("FAILED", output.result)
        self.assertEqual(
            fake.dispatched_actions, [],
            "Mixed persona + album Shuffle must reject whole call",
        )
        self.assertIsNotNone(output.errors)
        all_refs = set()
        for err in output.errors:
            all_refs.update(err.refs)
        self.assertIn("S:bbb01", all_refs)


if __name__ == "__main__":
    unittest.main()
