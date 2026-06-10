"""Tier 1 tests for reference resolution and cross-search flows.

Uses a FakeRoonBrowse that extends RoonBrowseMixin with a simulated
browse API, so we can test resolve_reference, compile_output, and
get_media_actions without a live Roon server.
"""

import unittest
from typing import Dict, List, Optional

from roon_core.browse import RoonBrowseMixin
from roon_core.browse_session import (
    ItemIdentity,
    SearchRecipe,
)
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreItemSummarySchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)

# ------------------------------------------------------------------ #
#  Fake Roon API transport                                           #
# ------------------------------------------------------------------ #

class FakeRoonApi:
    """Simulates the Roon browse API with realistic key prefix rotation.

    Like real Roon, item_keys have the form ``<prefix>:<position>`` where
    the prefix changes on every navigation event but the position is
    stable.  This ensures tests exercise the key-refresh logic that
    ``_position_session`` needs to handle.

    Tree keys in the constructor use the ``base:position`` format.  Each
    time items are loaded they are returned with a fresh prefix.
    ``browse_browse`` matches incoming keys by position suffix against the
    current items.
    """

    def __init__(
        self,
        tree: Dict[str, List[dict]],
        list_hints: Optional[Dict[str, str]] = None,
    ) -> None:
        """tree maps parent_key -> list of child item dicts.

        Special keys:
        - "__root__" is the top level (initial search results)
        - "__search__:<input>" is the result of a search with that input string
        - Each child dict has at minimum: {"title": ..., "item_key": ...}
          where item_key should use ``base:position`` format (e.g. "ik-a:0").

        list_hints maps parent_key -> list.hint value (e.g. "action_list")
        to simulate Roon's list-level hint metadata.
        """
        self.tree = tree
        self.list_hints = list_hints or {}
        self._gen = 0  # prefix generation counter
        # Per-session current items (with rotated keys)
        self._session_state: Dict[str, List[dict]] = {}
        # Per-session navigation stack for Roon API pop_levels
        self._session_stack: Dict[str, List[List[dict]]] = {}
        # Per-session current tree key (for list_hints lookup)
        self._session_key_at: Dict[str, Optional[str]] = {}
        # Mapping from current rotated key → canonical tree key
        self._live_key_map: Dict[str, str] = {}

    def _rotate_items(self, items: List[dict]) -> List[dict]:
        """Return copies of *items* with fresh key prefixes."""
        self._gen += 1
        result = []
        for item in items:
            copy = dict(item)
            canonical = copy.get("item_key", "")
            pos = canonical.rsplit(":", 1)[-1] if ":" in canonical else canonical
            fresh = f"g{self._gen}:{pos}"
            copy["item_key"] = fresh
            self._live_key_map[fresh] = canonical
            result.append(copy)
        return result

    def _resolve_key(self, key: str) -> Optional[str]:
        """Find the canonical tree key for a possibly-rotated key.

        Uses exact matching only (direct tree key or live_key_map lookup).
        Suffix matching is intentionally NOT done here — it would match
        items from different browse levels that happen to share a position.
        """
        # Direct match (canonical key passed in)
        if key in self.tree:
            return key
        # Rotated key — look up canonical via exact live_key_map entry
        canonical = self._live_key_map.get(key)
        if canonical and canonical in self.tree:
            return canonical
        return None

    def browse_browse(self, opts: dict) -> dict:
        session = opts.get("multi_session_key", "__default__")

        if opts.get("pop_all"):
            input_str = opts.get("input")
            if input_str:
                key = f"__search__:{input_str}"
                items = self.tree.get(key, [])
            else:
                key = "__root__"
                items = self.tree.get(key, [])
            self._session_state[session] = self._rotate_items(items)
            self._session_key_at[session] = key
            self._session_stack.setdefault(session, []).clear()
            return {"action": "list"}

        if opts.get("pop_levels"):
            levels = opts["pop_levels"]
            stack = self._session_stack.setdefault(session, [])
            for _ in range(min(levels, len(stack))):
                self._session_state[session] = stack.pop()
            self._session_key_at[session] = None
            return {"action": "list"}

        item_key = opts.get("item_key")
        if item_key:
            stack = self._session_stack.setdefault(session, [])
            stack.append(list(self._session_state.get(session, [])))
            canonical = self._resolve_key(item_key)

            if canonical is not None:
                children = self.tree.get(canonical, [])
                if children:
                    # Normal drill — has children in the tree
                    self._session_state[session] = self._rotate_items(children)
                    self._session_key_at[session] = canonical
                    return {"action": "list"}
                # Tree key with no children — leaf, auto-pop
                for _ in range(min(2, len(stack))):
                    self._session_state[session] = stack.pop()
                self._session_key_at[session] = None
                return {"action": "list"}

            # Not a tree key — check if it was returned as a browse item
            # (action items like "Play Now" are in live_key_map but have
            # no tree entry). Simulate Roon's action auto-pop: 2 levels.
            if item_key in self._live_key_map:
                for _ in range(min(2, len(stack))):
                    self._session_state[session] = stack.pop()
                self._session_key_at[session] = None
                return {"action": "list"}

            # Completely unknown key — error
            self._session_state[session] = []
            self._session_key_at[session] = None
            return {"is_error": True}

        return {"action": "list"}

    def browse_load(self, opts: dict) -> dict:
        session = opts.get("multi_session_key", "__default__")
        items = self._session_state.get(session, [])
        offset = opts.get("offset", 0)
        count = opts.get("count", 100)
        page = items[offset:offset + count]
        list_meta: dict = {"count": len(items), "title": "Results"}
        tree_key = self._session_key_at.get(session)
        if tree_key and tree_key in self.list_hints:
            list_meta["hint"] = self.list_hints[tree_key]
        return {"items": page, "list": list_meta}


class FakeRoonBrowse(RoonBrowseMixin):
    """A minimal object that satisfies RoonBrowseMixin's dependencies."""

    def __init__(
        self,
        tree: Dict[str, List[dict]],
        list_hints: Optional[Dict[str, str]] = None,
    ) -> None:
        self.api = FakeRoonApi(tree, list_hints=list_hints)
        self._zones: Dict[str, str] = {"Living Room": "output-1"}
        self._init_browse_session()

    def _lookup_output_id(self, zone: Optional[str] = None) -> str:
        return self._zones.get(zone or "Living Room", "output-1")


# ------------------------------------------------------------------ #
#  Helpers                                                           #
# ------------------------------------------------------------------ #

def _item(title, item_key, subtitle=None, hint=None):
    """Shortcut to build a dict for the fake tree.

    item_key should use ``base:position`` format (e.g. "ik-a:0") so
    FakeRoonApi can simulate realistic key prefix rotation.
    """
    d = {"title": title, "item_key": item_key}
    if subtitle:
        d["subtitle"] = subtitle
    if hint:
        d["hint"] = hint
    return d


def _make_action_items():
    """Standard action list items."""
    return [
        _item("Play Now", "action-play:0", hint="Action"),
        _item("Queue", "action-queue:1", hint="Action"),
        _item("Add Next", "action-next:2", hint="Action"),
    ]


# ------------------------------------------------------------------ #
#  Tests: compile_output                                             #
# ------------------------------------------------------------------ #

class TestCompileOutput(unittest.TestCase):

    def test_compile_output_mints_refs_for_all_items(self):
        roon = FakeRoonBrowse({})
        sk = roon.session_manager.new_search_session()
        roon.session_manager.set_current_list(sk, RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Track A", item_key="ik-a", source_group="Album X"),
                RoonCoreItemSchema(title="Track B", item_key="ik-b", source_group="Album X"),
            ],
            list=RoonCoreListSchema(count=2, title="Results"),
        ))
        groups = roon.compile_output(recipe=SearchRecipe(search_string="test"), session_key=sk)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].items), 2)
        # Each item should have a valid reference
        for item in groups[0].items:
            self.assertIsNotNone(item.reference)
            ref = roon.session_manager.get_ref(item.reference)
            self.assertIsNotNone(ref)

    def test_compile_output_reuses_existing_refs(self):
        roon = FakeRoonBrowse({})
        sk = roon.session_manager.new_search_session()
        roon.session_manager.set_current_list(sk, RoonCoreResultsSchema(
            items=[RoonCoreItemSchema(title="Track A", item_key="ik-a", source_group="-")],
            list=RoonCoreListSchema(count=1),
        ))
        groups1 = roon.compile_output(recipe=SearchRecipe(search_string="test"), session_key=sk)
        ref1 = groups1[0].items[0].reference

        # Compile again with same items — should reuse ref
        groups2 = roon.compile_output(recipe=SearchRecipe(search_string="test"), session_key=sk)
        ref2 = groups2[0].items[0].reference
        self.assertEqual(ref1, ref2)

    def test_compile_output_groups_by_source_group(self):
        roon = FakeRoonBrowse({})
        sk = roon.session_manager.new_search_session()
        roon.session_manager.set_current_list(sk, RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Track A", item_key="ik-a", source_group="Album X"),
                RoonCoreItemSchema(title="Track B", item_key="ik-b", source_group="Album Y"),
                RoonCoreItemSchema(title="Track C", item_key="ik-c", source_group="Album X"),
            ],
            list=RoonCoreListSchema(count=3),
        ))
        groups = roon.compile_output(recipe=SearchRecipe(search_string="test"), session_key=sk)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].group, "Album X")
        self.assertEqual(len(groups[0].items), 2)
        self.assertEqual(groups[1].group, "Album Y")
        self.assertEqual(len(groups[1].items), 1)

    def test_compile_output_uses_explicit_session_key(self):
        """Refs must be tagged with the explicit session_key."""
        roon = FakeRoonBrowse({})
        sk_a = roon.session_manager.new_search_session()
        roon.session_manager.new_search_session()  # sk_b — not used

        roon.session_manager.set_current_list(sk_a, RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Track A", item_key="ik-a:0", source_group="-"),
            ],
            list=RoonCoreListSchema(count=1, title="Results"),
        ))
        # Compile with explicit session_key=sk_a
        groups = roon.compile_output(
            recipe=SearchRecipe(search_string="test"), session_key=sk_a,
        )
        ref_id = groups[0].items[0].reference
        ref = roon.session_manager.get_ref(ref_id)
        self.assertEqual(ref.roon_session_key, sk_a)

    def test_compile_output_raises_without_session_key(self):
        """compile_output requires an explicit session_key."""
        roon = FakeRoonBrowse({})
        sk = roon.session_manager.new_search_session()
        roon.session_manager.set_current_list(sk, RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Track A", item_key="ik-a:0", source_group="-"),
            ],
            list=RoonCoreListSchema(count=1, title="Results"),
        ))
        with self.assertRaises(ValueError):
            roon.compile_output(recipe=SearchRecipe(search_string="test"))

    def test_parallel_searches_compile_to_correct_sessions(self):
        """Full parallel-search scenario: two searches, drill-downs compile
        with correct session tags even though active_session_key changed."""
        tree = {
            "__search__:alpha": [_item("Cat A", "ik-ca:0")],
            "__search__:beta": [_item("Cat B", "ik-cb:0")],
            "ik-ca:0": [_item("Track A1", "ik-ta1:0"), _item("Track A2", "ik-ta2:1")],
            "ik-cb:0": [_item("Track B1", "ik-tb1:0"), _item("Track B2", "ik-tb2:1")],
        }
        roon = FakeRoonBrowse(tree)

        # Search A
        sk_a = roon.session_manager.new_search_session()
        roon.browse_core({"pop_all": True, "input": "alpha"}, session_key=sk_a)
        groups_a_root = roon.compile_output(
            recipe=SearchRecipe(search_string="alpha"), session_key=sk_a,
        )
        ref_cat_a = groups_a_root[0].items[0].reference

        # Search B
        sk_b = roon.session_manager.new_search_session()
        roon.browse_core({"pop_all": True, "input": "beta"}, session_key=sk_b)
        roon.compile_output(
            recipe=SearchRecipe(search_string="beta"), session_key=sk_b,
        )

        # Drill into Cat A using explicit session key
        ref_a = roon.session_manager.get_ref(ref_cat_a)
        temp_item_a = RoonCoreItemSchema(
            title=ref_a.identity.title,
            item_key=ref_a.cached_item_key,
            item_key_path=list(ref_a.item_key_path),
        )
        roon.drill_down(drilldown_item=temp_item_a, session_key=sk_a)
        groups_a_drill = roon.compile_output(
            recipe=SearchRecipe(search_string="alpha"), session_key=sk_a,
        )

        # All refs from the drill-down must point to sk_a, not sk_b
        for group in groups_a_drill:
            for item in group.items:
                ref = roon.session_manager.get_ref(item.reference)
                self.assertEqual(
                    ref.roon_session_key, sk_a,
                    f"Ref {item.reference} ({item.title}) tagged with "
                    f"{ref.roon_session_key}, expected {sk_a}",
                )


# ------------------------------------------------------------------ #
#  Tests: resolve_reference                                          #
# ------------------------------------------------------------------ #

class TestResolveReference(unittest.TestCase):

    def _setup_roon_with_search(self):
        """Set up a FakeRoonBrowse with a search that produces tracks."""
        tree = {
            "__search__:rock": [
                _item("Tracks", "cat-tracks:0"),
            ],
            "cat-tracks:0": [
                _item("Track A", "ik-a:0", subtitle="Artist 1"),
                _item("Track B", "ik-b:1", subtitle="Artist 2"),
                _item("Track C", "ik-c:2", subtitle="Artist 3"),
            ],
        }
        roon = FakeRoonBrowse(tree)
        return roon

    def test_tier1_live_reference_resolves(self):
        roon = self._setup_roon_with_search()
        sk = roon.session_manager.new_search_session()

        # Simulate a search and compile output
        roon.browse_core(
            aux={"pop_all": True, "input": "rock"},
            session_key=sk,
        )
        cat = roon.session_manager.get_current_list(sk).items[0]
        roon.browse_core(aux={"item_key": cat.item_key}, session_key=sk)

        roon.compile_output(recipe=SearchRecipe(search_string="rock", category="Tracks"), session_key=sk)
        # Get first ref
        ref_id = list(roon.session_manager.refs.keys())[0]

        # Resolve — should succeed via Tier 1 (live key)
        resolved = roon.resolve_reference(ref_id)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.cached_item_key.endswith(":0"))

    def test_tier1_succeeds_after_new_session(self):
        """With multi_session_key isolation, Tier 1 resolves across sessions."""
        roon = self._setup_roon_with_search()
        sk = roon.session_manager.new_search_session()

        roon.browse_core(
            aux={"pop_all": True, "input": "rock"},
            session_key=sk,
        )
        cat = roon.session_manager.get_current_list(sk).items[0]
        roon.browse_core(aux={"item_key": cat.item_key}, session_key=sk)
        roon.compile_output(recipe=SearchRecipe(search_string="rock", category="Tracks"), session_key=sk)
        ref_id = list(roon.session_manager.refs.keys())[0]

        # Start a new session — Tier 1 should still succeed (session is tracked)
        roon.session_manager.new_search_session()
        ref = roon.session_manager.get_ref(ref_id)
        self.assertTrue(roon.session_manager.is_key_live(ref))

        # Full resolution should work too
        resolved = roon.resolve_reference(ref_id)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.cached_item_key.endswith(":0"))

    def test_semantic_recovery_for_unknown_session(self):
        """Semantic recovery kicks in when the session is not tracked."""
        tree = {
            "__search__:rock": [
                _item("Tracks", "cat-tracks:0"),
            ],
            "cat-tracks:0": [
                _item("Track A", "ik-a-new:0", subtitle="Artist 1"),
                _item("Track B", "ik-b-new:1", subtitle="Artist 2"),
            ],
        }
        roon = FakeRoonBrowse(tree)

        # Manually mint a ref on an unknown session (simulating a dead session
        # e.g. after reconnect)
        ref_id = roon.session_manager.mint_ref(
            identity=ItemIdentity(title="Track A", subtitle="Artist 1"),
            recipe=SearchRecipe(search_string="rock", category="Tracks"),
            item_key="ik-a-old:0",
            session_key="dead-session",
        )

        # Semantic recovery should re-search and find the item
        resolved = roon.resolve_reference(ref_id)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.cached_item_key.endswith(":0"))

    def test_resolution_failure_returns_none(self):
        roon = FakeRoonBrowse({})
        result = roon.resolve_reference("nonexistent")
        self.assertIsNone(result)

    def test_resolution_failure_for_unknown_session_no_match(self):
        """If re-search produces no matching items, resolution fails."""
        tree = {
            "__search__:rock": [
                _item("Tracks", "cat-tracks:0"),
            ],
            "cat-tracks:0": [
                _item("Completely Different", "ik-x:0", subtitle="Other Artist"),
            ],
        }
        roon = FakeRoonBrowse(tree)

        # Mint a ref on an unknown session with a title that won't match
        ref_id = roon.session_manager.mint_ref(
            identity=ItemIdentity(title="Nonexistent Track"),
            recipe=SearchRecipe(search_string="rock", category="Tracks"),
            item_key="ik-gone:0",
            session_key="dead-session",
        )

        resolved = roon.resolve_reference(ref_id)
        self.assertIsNone(resolved)


# ------------------------------------------------------------------ #
#  Tests: cross-search reference flows                               #
# ------------------------------------------------------------------ #

class TestCrossSearchReferences(unittest.TestCase):
    """Test the key R1 4.1 scenario: references surviving across searches."""

    def _make_roon(self):
        tree = {
            "__search__:artist a": [
                _item("Track A1", "ik-a1:0", subtitle="Artist A"),
                _item("Track A2", "ik-a2:1", subtitle="Artist A"),
            ],
            "__search__:artist b": [
                _item("Track B1", "ik-b1:0", subtitle="Artist B"),
                _item("Track B2", "ik-b2:1", subtitle="Artist B"),
            ],
            "__search__:artist c": [
                _item("Track C1", "ik-c1:0", subtitle="Artist C"),
            ],
            # Action lists for each track
            "ik-a1:0": _make_action_items(),
            "ik-a2:1": _make_action_items(),
            "ik-b1:0": _make_action_items(),
            "ik-b2:1": _make_action_items(),
            "ik-c1:0": _make_action_items(),
        }
        return FakeRoonBrowse(tree)

    def _do_search(self, roon, query):
        """Perform a search and compile output, return the ref IDs."""
        sk = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": query},
            session_key=sk,
        )
        groups = roon.compile_output(recipe=SearchRecipe(search_string=query), session_key=sk)
        refs = []
        for group in groups:
            for item in group.items:
                refs.append(item.reference)
        return refs

    def test_three_searches_then_batched_resolve(self):
        """The exact R1 4.1 scenario — three searches, then resolve one from each."""
        roon = self._make_roon()
        refs_a = self._do_search(roon, "artist a")
        refs_b = self._do_search(roon, "artist b")
        refs_c = self._do_search(roon, "artist c")

        # Now on search-3 — refs from search-1 and search-2 are stale
        results = []
        for ref_id in [refs_a[0], refs_b[0], refs_c[0]]:
            resolved = roon.resolve_reference(ref_id)
            results.append(resolved)

        self.assertIsNotNone(results[0], "Ref from search A should resolve")
        self.assertIsNotNone(results[1], "Ref from search B should resolve")
        self.assertIsNotNone(results[2], "Ref from search C should resolve")
        self.assertEqual(results[0].identity.title, "Track A1")
        self.assertEqual(results[1].identity.title, "Track B1")
        self.assertEqual(results[2].identity.title, "Track C1")


# ------------------------------------------------------------------ #
#  Tests: get_media_actions                                          #
# ------------------------------------------------------------------ #

class TestGetMediaActions(unittest.TestCase):

    def test_get_media_actions_for_live_track(self):
        tree = {
            "__search__:rock": [
                _item("Track A", "ik-a:0", subtitle="Artist 1"),
            ],
            "ik-a:0": _make_action_items(),
        }
        roon = FakeRoonBrowse(tree)
        sk = roon.session_manager.new_search_session()
        roon.browse_core(aux={"pop_all": True, "input": "rock"}, session_key=sk)
        groups = roon.compile_output(recipe=SearchRecipe(search_string="rock"), session_key=sk)
        media_item = groups[0].items[0]

        results, session_key, _ = roon.get_media_actions(media_item)
        self.assertIsNotNone(results)
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)
        self.assertIn("Queue", action_titles)

    def test_get_media_actions_after_session_change(self):
        """Actions should still be obtainable for a ref from a prior session."""
        tree = {
            "__search__:rock": [
                _item("Track A", "ik-a:0", subtitle="Artist 1"),
            ],
            "__search__:jazz": [
                _item("Track J", "ik-j:0", subtitle="Artist 2"),
            ],
            "ik-a:0": _make_action_items(),
            "ik-j:0": _make_action_items(),
        }
        roon = FakeRoonBrowse(tree)

        # Search 1
        sk1 = roon.session_manager.new_search_session()
        roon.browse_core(aux={"pop_all": True, "input": "rock"}, session_key=sk1)
        groups = roon.compile_output(recipe=SearchRecipe(search_string="rock"), session_key=sk1)
        media_item_a = groups[0].items[0]

        # Search 2 — new session
        sk2 = roon.session_manager.new_search_session()
        roon.browse_core(aux={"pop_all": True, "input": "jazz"}, session_key=sk2)

        # Try to get actions for Track A (from search-1)
        results, session_key, _ = roon.get_media_actions(media_item_a)
        self.assertIsNotNone(results, "Should get actions for ref from prior session")
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_get_media_actions_unresolvable_ref(self):
        roon = FakeRoonBrowse({})
        media_item = RoonCoreItemSummarySchema(
            title="Ghost Track", reference="nonexistent",
        )
        results, sk, _ = roon.get_media_actions(media_item)
        self.assertIsNone(results)
        self.assertIsNone(sk)


# ------------------------------------------------------------------ #
#  Tests: get_media_actions with realistic Roon hierarchy depths      #
# ------------------------------------------------------------------ #

def _make_deep_track_tree():
    """Build a tree that mirrors real Roon hierarchy for a track search.

    Real Roon hierarchy (confirmed via live diagnostics):
      Search root → [top_result(action_list), Albums(list), Tracks(list)]
      Tracks category → [Track A(action_list), Track B(action_list), ...]
      Track A → [Track A (duplicate, action_list)]      ← single item
      Track A duplicate → [Play Now, Add Next, Queue]   ← action_list
    """
    return {
        "__search__:test track": [
            _item("Test Track", "top-result:0", hint="action_list", subtitle="Artist X"),
            _item("Albums", "cat-albums:1", hint="list", subtitle="3 Results"),
            _item("Tracks", "cat-tracks:2", hint="list", subtitle="5 Results"),
        ],
        # Tracks category contains multiple variants (all action_list hint)
        "cat-tracks:2": [
            _item("Test Track", "tk-1:0", hint="action_list", subtitle="Artist X"),
            _item("Test Track (Live)", "tk-2:1", hint="action_list", subtitle="Artist X"),
            _item("Test Track (Remix)", "tk-3:2", hint="action_list", subtitle="DJ Y"),
        ],
        # Drilling into a track variant → duplicate level
        "tk-1:0": [
            _item("Test Track", "tk-1-dup:0", hint="action_list", subtitle="Artist X"),
        ],
        # Drilling into the duplicate → action list
        "tk-1-dup:0": [
            _item("Play Now", "action-play:0", hint="action"),
            _item("Add Next", "action-next:1", hint="action"),
            _item("Queue", "action-queue:2", hint="action"),
        ],
        # Top result follows the same depth pattern
        "top-result:0": [
            _item("Test Track", "top-dup:0", hint="action_list", subtitle="Artist X"),
        ],
        "top-dup:0": [
            _item("Play Now", "action-play-top:0", hint="action"),
            _item("Add Next", "action-next-top:1", hint="action"),
            _item("Queue", "action-queue-top:2", hint="action"),
        ],
    }


def _deep_track_list_hints():
    """list.hint metadata for deep track tree levels."""
    return {
        "tk-1-dup:0": "action_list",
        "top-dup:0": "action_list",
    }


class TestGetMediaActionsDeepHierarchy(unittest.TestCase):
    """Tests that get_media_actions drills through realistic Roon hierarchies."""

    def test_action_via_tracks_category(self):
        """Track found by drilling into the Tracks category should reach actions.

        Path: search → Tracks category → track variants → pick best match
        → duplicate → action list.
        """
        tree = _make_deep_track_tree()
        roon = FakeRoonBrowse(tree, list_hints=_deep_track_list_hints())

        # Search and drill into Tracks category
        sk = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": "test track"},
            session_key=sk,
        )
        # Drill into Tracks category
        tracks_cat = roon.find_item_by_field(
            roon.session_manager.get_current_list(sk).items, "title", "Tracks",
        )
        recipe = SearchRecipe(search_string="test track", category="Tracks")
        roon.drill_down(drilldown_item=tracks_cat, recipe=recipe, session_key=sk)
        groups = roon.compile_output(recipe=recipe, session_key=sk)

        # Pick the first track
        media_item = groups[0].items[0]
        self.assertEqual(media_item.title, "Test Track")

        results, session_key, levels_pushed = roon.get_media_actions(media_item)

        self.assertIsNotNone(results)
        self.assertEqual(results.list.hint, "action_list")
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)
        self.assertIn("Queue", action_titles)
        self.assertGreater(levels_pushed, 1, "Should drill multiple levels")

    def test_action_via_top_result(self):
        """Top result from search root should also reach actions."""
        tree = _make_deep_track_tree()
        roon = FakeRoonBrowse(tree, list_hints=_deep_track_list_hints())

        sk = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": "test track"},
            session_key=sk,
        )
        groups = roon.compile_output(
            recipe=SearchRecipe(search_string="test track"),
            session_key=sk,
        )

        # The top result is "Test Track" (first item, not a category)
        top_item = groups[0].items[0]
        self.assertEqual(top_item.title, "Test Track")

        results, session_key, levels_pushed = roon.get_media_actions(top_item)

        self.assertIsNotNone(results)
        self.assertEqual(results.list.hint, "action_list")
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_action_via_variant_groupings(self):
        """When all items have hint='action_list', drill into best match."""
        tree = {
            "__search__:test": [
                _item("My Song", "variant-parent:0", hint="action_list", subtitle="Artist"),
            ],
            # Variants (all action_list) — not yet the real action menu
            "variant-parent:0": [
                _item("My Song", "v1:0", hint="action_list", subtitle="Artist"),
                _item("My Song (Remaster)", "v2:1", hint="action_list", subtitle="Artist"),
            ],
            "v1:0": [
                _item("Play Now", "a-play:0", hint="action"),
                _item("Queue", "a-queue:1", hint="action"),
            ],
        }
        hints = {"v1:0": "action_list"}
        roon = FakeRoonBrowse(tree, list_hints=hints)

        sk = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": "test"},
            session_key=sk,
        )
        groups = roon.compile_output(
            recipe=SearchRecipe(search_string="test"),
            session_key=sk,
        )
        media_item = groups[0].items[0]

        results, _, levels_pushed = roon.get_media_actions(media_item)

        self.assertIsNotNone(results)
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_action_via_gateway_item(self):
        """'Play Album' gateway items should be drilled through."""
        tree = {
            "__search__:album": [
                _item("My Album", "alb-1:0", hint="list", subtitle="Artist"),
            ],
            "alb-1:0": [
                _item("Play Album", "gateway:0", hint="action"),
                _item("Track 1", "t1:1", hint="action_list"),
                _item("Track 2", "t2:2", hint="action_list"),
            ],
            "gateway:0": [
                _item("Play Now", "a-play:0", hint="action"),
                _item("Queue", "a-queue:1", hint="action"),
            ],
        }
        hints = {"gateway:0": "action_list"}
        roon = FakeRoonBrowse(tree, list_hints=hints)

        sk = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": "album"},
            session_key=sk,
        )
        groups = roon.compile_output(
            recipe=SearchRecipe(search_string="album"),
            session_key=sk,
        )
        media_item = groups[0].items[0]

        results, _, _ = roon.get_media_actions(media_item)

        self.assertIsNotNone(results)
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_stops_when_no_drill_target(self):
        """Should return whatever it has if it can't determine a drill target."""
        tree = {
            "__search__:odd": [
                _item("Odd Item", "odd-1:0", hint="list"),
            ],
            # Drilling in gives items with no recognisable pattern
            "odd-1:0": [
                _item("Something", "x1:0", hint="list"),
                _item("Else", "x2:1", hint="list"),
            ],
        }
        roon = FakeRoonBrowse(tree)

        sk = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": "odd"},
            session_key=sk,
        )
        groups = roon.compile_output(
            recipe=SearchRecipe(search_string="odd"),
            session_key=sk,
        )
        media_item = groups[0].items[0]

        results, _, _ = roon.get_media_actions(media_item)

        # Should return the intermediate results, not None
        self.assertIsNotNone(results)
        # But it won't be an action list
        list_hint = results.list.hint if results.list else None
        self.assertNotEqual(list_hint, "action_list")


# ------------------------------------------------------------------ #
#  Tests: playlist drill-down (deep item_key_path)                    #
# ------------------------------------------------------------------ #

class TestPlaylistDrillDown(unittest.TestCase):
    """Regression tests: references from drilling into a playlist must
    support get_media_actions even after session changes.

    The key behaviour: a 3-level item_key_path (search → playlist → track)
    must survive pop-to-root and re-walk with key prefix rotation.
    """

    def _make_playlist_roon(self):
        tree = {
            "__search__:favourites": [
                _item("Favourites", "pl-fav:0", subtitle="62 Tracks"),
            ],
            "pl-fav:0": [
                _item("Play Playlist", "play-pl:0", hint="action"),
                _item("Pretty Green Eyes", "tk-pge:1", subtitle="Ultrabeat"),
                _item("Outta Time", "tk-ot:2", subtitle="Whelan & Di Scala"),
                _item("Rock My Body", "tk-rmb:3", subtitle="R3HAB, INNA"),
            ],
            # Drilling into a playlist track → action list
            "tk-pge:1": _make_action_items(),
            "tk-ot:2": _make_action_items(),
            "tk-rmb:3": _make_action_items(),
        }
        hints = {
            "tk-pge:1": "action_list",
            "tk-ot:2": "action_list",
            "tk-rmb:3": "action_list",
        }
        return FakeRoonBrowse(tree, list_hints=hints)

    def test_actions_on_playlist_track_after_new_search(self):
        """Playlist track ref should still yield actions after a second search."""
        roon = self._make_playlist_roon()
        sk1 = roon.session_manager.new_search_session()

        # Search 1 → drill into playlist
        roon.browse_core(
            aux={"pop_all": True, "input": "favourites"},
            session_key=sk1,
        )
        playlist_item = roon.session_manager.get_current_list(sk1).items[0]
        recipe = SearchRecipe(
            search_string="favourites",
            parent_chain=[ItemIdentity(title="Favourites", subtitle="62 Tracks")],
        )
        roon.drill_down(drilldown_item=playlist_item, recipe=recipe, session_key=sk1)
        groups = roon.compile_output(recipe=recipe, session_key=sk1)
        track_item = next(i for i in groups[0].items if i.title == "Rock My Body")

        # Search 2 — different session
        sk2 = roon.session_manager.new_search_session()
        roon.browse_core(
            aux={"pop_all": True, "input": "something else"},
            session_key=sk2,
        )

        # Get actions on the playlist track from search 1
        results, _, _ = roon.get_media_actions(track_item)

        self.assertIsNotNone(results, "Playlist track ref should survive session change")
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_actions_on_multiple_playlist_tracks(self):
        """Multiple tracks from the same playlist should all yield actions."""
        roon = self._make_playlist_roon()
        sk = roon.session_manager.new_search_session()

        roon.browse_core(
            aux={"pop_all": True, "input": "favourites"},
            session_key=sk,
        )
        playlist_item = roon.session_manager.get_current_list(sk).items[0]
        recipe = SearchRecipe(
            search_string="favourites",
            parent_chain=[ItemIdentity(title="Favourites", subtitle="62 Tracks")],
        )
        roon.drill_down(drilldown_item=playlist_item, recipe=recipe, session_key=sk)
        groups = roon.compile_output(recipe=recipe, session_key=sk)

        # Try get_media_actions on all three tracks
        for title in ["Pretty Green Eyes", "Outta Time", "Rock My Body"]:
            track_item = next(i for i in groups[0].items if i.title == title)
            results, _, _ = roon.get_media_actions(track_item)
            self.assertIsNotNone(results, f"Should get actions for '{title}'")
            action_titles = [i.title for i in results.items]
            self.assertIn("Play Now", action_titles, f"'{title}' should have Play Now")


# ------------------------------------------------------------------ #
#  Tests: multi-item action sequences                                 #
# ------------------------------------------------------------------ #

def _make_multi_item_tree():
    """Tree with a playlist containing tracks, each with action lists.

    Mirrors a drill scenario: search → Playlists → Favourites → tracks.
    Positions chosen to avoid accidental suffix collisions.
    """
    return {
        "__search__:favourites": [
            _item("Artists", "cat-art:0"),
            _item("Albums", "cat-alb:1"),
            _item("Tracks", "cat-trk:2"),
            _item("Playlists", "cat-pl:3"),
        ],
        "cat-pl:3": [
            _item("Favourites", "pl-fav:0", subtitle="4 Tracks"),
        ],
        "pl-fav:0": [
            _item("Play Playlist", "play-pl:0", hint="action"),
            _item("Track A", "tk-a:1", subtitle="Artist A"),
            _item("Track B", "tk-b:2", subtitle="Artist B"),
            _item("Track C", "tk-c:3", subtitle="Artist C"),
        ],
        "tk-a:1": _make_action_items(),
        "tk-b:2": _make_action_items(),
        "tk-c:3": _make_action_items(),
    }


def _multi_item_hints():
    return {
        "tk-a:1": "action_list",
        "tk-b:2": "action_list",
        "tk-c:3": "action_list",
    }


def _setup_playlist_tracks(roon):
    """Search, drill into Playlists → Favourites, return compiled track items."""
    sk = roon.session_manager.new_search_session()
    roon.browse_core(
        aux={"pop_all": True, "input": "favourites"},
        session_key=sk,
    )
    # Drill into Playlists category
    playlists_cat = roon.find_item_by_field(
        roon.session_manager.get_current_list(sk).items, "title", "Playlists",
    )
    recipe = SearchRecipe(search_string="favourites", category="Playlists")
    roon.drill_down(drilldown_item=playlists_cat, recipe=recipe, session_key=sk)
    # Drill into Favourites playlist
    fav_item = roon.session_manager.get_current_list(sk).items[0]
    recipe = SearchRecipe(
        search_string="favourites",
        category="Playlists",
        parent_chain=[ItemIdentity(title="Favourites", subtitle="4 Tracks")],
    )
    roon.drill_down(drilldown_item=fav_item, recipe=recipe, session_key=sk)
    groups = roon.compile_output(recipe=recipe, session_key=sk)
    return sk, groups


def _simulate_action_execution(roon, results, session_key, levels_pushed):
    """Simulate what roon_action does: execute action then reset to root."""
    play_now = next(i for i in results.items if i.title == "Play Now")
    # Execute action — FakeRoonApi auto-pops 2 levels for leaf items
    roon.browse_core(
        aux={"item_key": play_now.item_key},
        zone=None,
        session_key=session_key,
        update_current=False,
    )
    # Reset to root — matches the rewritten roon_action.py behaviour
    roon._nav_reset_to_root(session_key)


class TestMultiItemActionSequence(unittest.TestCase):
    """Regression tests: after executing an action on item 1,
    get_media_actions on item 2 from the same session should succeed via the
    fast path (Tier 1), not require semantic recovery.
    """

    def test_second_item_resolves_after_first_actioned(self):
        """get_media_actions should succeed for item 2 after item 1 is actioned."""
        roon = FakeRoonBrowse(
            _make_multi_item_tree(), list_hints=_multi_item_hints(),
        )
        sk, groups = _setup_playlist_tracks(roon)

        track_a = next(i for i in groups[0].items if i.title == "Track A")
        track_b = next(i for i in groups[0].items if i.title == "Track B")

        # Action track A
        results_a, sk_a, lp_a = roon.get_media_actions(track_a)
        self.assertIsNotNone(results_a)
        _simulate_action_execution(roon, results_a, sk_a, lp_a)

        # Track B should still resolve
        results_b, _, _ = roon.get_media_actions(track_b)
        self.assertIsNotNone(results_b, "Track B should resolve after Track A actioned")
        action_titles = [i.title for i in results_b.items]
        self.assertIn("Play Now", action_titles)

    def test_three_items_actioned_sequentially(self):
        """All three items should resolve when actioned in sequence."""
        roon = FakeRoonBrowse(
            _make_multi_item_tree(), list_hints=_multi_item_hints(),
        )
        sk, groups = _setup_playlist_tracks(roon)

        tracks = ["Track A", "Track B", "Track C"]
        for i, title in enumerate(tracks):
            track = next(item for item in groups[0].items if item.title == title)
            results, session_key, levels_pushed = roon.get_media_actions(track)
            self.assertIsNotNone(
                results,
                f"{title} (item {i + 1}) should resolve",
            )
            action_titles = [item.title for item in results.items]
            self.assertIn("Play Now", action_titles)
            _simulate_action_execution(roon, results, session_key, levels_pushed)


# ------------------------------------------------------------------ #
#  Tests: depth tracker accuracy after action execution               #
# ------------------------------------------------------------------ #

class TestDepthTrackingAfterAction(unittest.TestCase):
    """Verify the session depth tracker stays in sync with actual browse state."""

    def test_depth_matches_after_single_action(self):
        """After get_media_actions + action + pop, tracked depth should match
        the actual FakeRoonApi stack depth.
        """
        roon = FakeRoonBrowse(
            _make_multi_item_tree(), list_hints=_multi_item_hints(),
        )
        sk, groups = _setup_playlist_tracks(roon)

        track_a = next(i for i in groups[0].items if i.title == "Track A")

        results, session_key, levels_pushed = roon.get_media_actions(track_a)
        self.assertIsNotNone(results)
        _simulate_action_execution(roon, results, session_key, levels_pushed)

        tracked = roon.session_manager.get_session_depth(sk)
        actual = len(roon.api._session_stack.get(sk, []))
        self.assertEqual(
            tracked,
            actual,
            f"Tracked depth ({tracked}) should match actual stack depth ({actual})",
        )


# ------------------------------------------------------------------ #
#  Tests: path building with repeated position suffixes               #
# ------------------------------------------------------------------ #

class TestPathBuildingRepeatedSuffixes(unittest.TestCase):
    """Verify item_key_path is built and walked correctly when position
    suffixes repeat across levels (e.g. position 0 at every level)."""

    def _make_repeated_suffix_tree(self):
        """Tree where position 0 appears at every level."""
        return {
            "__search__:test": [
                _item("Category A", "cat-a:0"),
                _item("Category B", "cat-b:1"),
            ],
            "cat-a:0": [
                _item("Sub A", "sub-a:0"),
                _item("Sub B", "sub-b:1"),
            ],
            "sub-a:0": [
                _item("Item X", "item-x:0"),
                _item("Item Y", "item-y:1"),
            ],
            "item-x:0": _make_action_items(),
            "item-y:1": _make_action_items(),
        }

    def test_path_includes_all_positions(self):
        """item_key_path should contain all positions, even when suffixes repeat."""
        roon = FakeRoonBrowse(
            self._make_repeated_suffix_tree(),
            list_hints={"item-x:0": "action_list", "item-y:1": "action_list"},
        )
        sk = roon.session_manager.new_search_session()

        roon.browse_core(
            aux={"pop_all": True, "input": "test"}, session_key=sk,
        )
        # Drill: root → Category A (position 0)
        cat_a = roon.find_item_by_field(roon.session_manager.get_current_list(sk).items, "title", "Category A")
        recipe = SearchRecipe(search_string="test")
        roon.drill_down(drilldown_item=cat_a, recipe=recipe, session_key=sk)

        # Drill: Category A → Sub A (position 0)
        sub_a = roon.find_item_by_field(roon.session_manager.get_current_list(sk).items, "title", "Sub A")
        roon.drill_down(drilldown_item=sub_a, recipe=recipe, session_key=sk)

        groups = roon.compile_output(recipe=recipe, session_key=sk)
        item_x = next(i for i in groups[0].items if i.title == "Item X")
        ref = roon.session_manager.get_ref(item_x.reference)

        # Path should be [0, 0, 0] — position 0 at each of the three levels
        self.assertEqual(
            ref.item_key_path,
            ["0", "0", "0"],
            "Path should preserve all positions even when suffixes repeat",
        )

    def test_resolve_walks_repeated_suffix_path(self):
        """resolve_reference should walk a path with repeated suffixes correctly."""
        roon = FakeRoonBrowse(
            self._make_repeated_suffix_tree(),
            list_hints={"item-x:0": "action_list", "item-y:1": "action_list"},
        )
        sk = roon.session_manager.new_search_session()

        roon.browse_core(
            aux={"pop_all": True, "input": "test"}, session_key=sk,
        )
        cat_a = roon.find_item_by_field(roon.session_manager.get_current_list(sk).items, "title", "Category A")
        recipe = SearchRecipe(search_string="test")
        roon.drill_down(drilldown_item=cat_a, recipe=recipe, session_key=sk)
        sub_a = roon.find_item_by_field(roon.session_manager.get_current_list(sk).items, "title", "Sub A")
        roon.drill_down(drilldown_item=sub_a, recipe=recipe, session_key=sk)
        groups = roon.compile_output(recipe=recipe, session_key=sk)
        item_x = next(i for i in groups[0].items if i.title == "Item X")

        # Resolve should succeed — walks path ["0", "0", "0"]
        results, _, levels_pushed = roon.get_media_actions(item_x)
        self.assertIsNotNone(results, "Should resolve item via path with repeated suffixes")
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_sibling_at_different_position_also_resolves(self):
        """Item Y (position 1) alongside Item X (position 0) should also resolve."""
        roon = FakeRoonBrowse(
            self._make_repeated_suffix_tree(),
            list_hints={"item-x:0": "action_list", "item-y:1": "action_list"},
        )
        sk = roon.session_manager.new_search_session()

        roon.browse_core(
            aux={"pop_all": True, "input": "test"}, session_key=sk,
        )
        cat_a = roon.find_item_by_field(roon.session_manager.get_current_list(sk).items, "title", "Category A")
        recipe = SearchRecipe(search_string="test")
        roon.drill_down(drilldown_item=cat_a, recipe=recipe, session_key=sk)
        sub_a = roon.find_item_by_field(roon.session_manager.get_current_list(sk).items, "title", "Sub A")
        roon.drill_down(drilldown_item=sub_a, recipe=recipe, session_key=sk)
        groups = roon.compile_output(recipe=recipe, session_key=sk)

        item_y = next(i for i in groups[0].items if i.title == "Item Y")
        ref = roon.session_manager.get_ref(item_y.reference)
        self.assertEqual(ref.item_key_path, ["0", "0", "1"])

        results, _, _ = roon.get_media_actions(item_y)
        self.assertIsNotNone(results)
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)


if __name__ == "__main__":
    unittest.main()
