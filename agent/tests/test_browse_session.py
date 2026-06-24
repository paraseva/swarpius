"""Tier 1 unit tests for BrowseSessionManager — no Roon server required."""

import unittest
from unittest.mock import patch

from roon_core.browse_session import (
    BrowseSessionManager,
    ItemIdentity,
    SearchRecipe,
    StableReference,
)


class TestNewSearchSession(unittest.TestCase):

    def test_new_search_session_increments_key(self):
        mgr = BrowseSessionManager()
        k1 = mgr.new_search_session()
        k2 = mgr.new_search_session()
        k3 = mgr.new_search_session()
        # Keys must be unique and sequential
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k2, k3)

    def test_session_keys_unique_across_instances(self):
        """Session keys include a random prefix so they don't collide
        with Roon Core's cached sessions after a server restart."""
        mgr_a = BrowseSessionManager()
        mgr_b = BrowseSessionManager()
        key_a = mgr_a.new_search_session()
        key_b = mgr_b.new_search_session()
        self.assertNotEqual(key_a, key_b)

    def test_action_session_key_is_constant(self):
        mgr = BrowseSessionManager()
        self.assertEqual(mgr.action_session_key, "action")

    def test_recovery_session_key_is_constant(self):
        mgr = BrowseSessionManager()
        self.assertEqual(mgr.recovery_session_key, "recovery")


class TestSessionRecycling(unittest.TestCase):
    """Session keys cycle through a fixed pool and purge stale refs on reuse."""

    def test_keys_cycle_after_max_sessions(self):
        mgr = BrowseSessionManager(max_sessions=4)
        keys = [mgr.new_search_session() for _ in range(5)]
        # 5th key should equal the 1st (recycled)
        self.assertEqual(keys[4], keys[0])
        # First 4 should all be distinct
        self.assertEqual(len(set(keys[:4])), 4)

    def test_refs_purged_on_recycle(self):
        mgr = BrowseSessionManager(max_sessions=2)
        sk1 = mgr.new_search_session()
        ref_id = mgr.mint_ref(
            identity=ItemIdentity(title="Track A"),
            recipe=SearchRecipe(search_string="track a"),
            item_key="ik-1",
            session_key=sk1,
        )
        self.assertIsNotNone(mgr.get_ref(ref_id))

        # Use up the pool: sk2, then sk1 again
        mgr.new_search_session()  # sk2
        mgr.new_search_session()  # recycles sk1

        # Ref pointing to old sk1 should be purged
        self.assertIsNone(mgr.get_ref(ref_id))

    def test_refs_on_other_sessions_preserved(self):
        mgr = BrowseSessionManager(max_sessions=4)
        sk1 = mgr.new_search_session()
        ref1 = mgr.mint_ref(
            identity=ItemIdentity(title="Track A"),
            recipe=SearchRecipe(search_string="a"),
            item_key="ik-1",
            session_key=sk1,
        )
        sk2 = mgr.new_search_session()
        ref2 = mgr.mint_ref(
            identity=ItemIdentity(title="Track B"),
            recipe=SearchRecipe(search_string="b"),
            item_key="ik-2",
            session_key=sk2,
        )

        # Cycle through remaining slots + recycle sk1
        mgr.new_search_session()  # slot 2
        mgr.new_search_session()  # slot 3
        mgr.new_search_session()  # recycles slot 0 (sk1)

        # ref1 (on sk1) purged, ref2 (on sk2) preserved
        self.assertIsNone(mgr.get_ref(ref1))
        self.assertIsNotNone(mgr.get_ref(ref2))

class TestMintRef(unittest.TestCase):

    def _make_mgr(self, **kwargs):
        mgr = BrowseSessionManager(**kwargs)
        mgr.new_search_session()
        return mgr

    def _identity(self, title="Track A"):
        return ItemIdentity(title=title, subtitle="Artist X")

    def _recipe(self, search_string="track a"):
        return SearchRecipe(search_string=search_string)

    def test_mint_ref_stores_reference(self):
        mgr = self._make_mgr()
        sk = mgr.new_search_session()
        ref_id = mgr.mint_ref(
            identity=self._identity(),
            recipe=self._recipe(),
            item_key="ik-100",
            session_key=sk,
        )
        self.assertEqual(len(ref_id), 5)
        self.assertTrue(all(c in "0123456789abcdef" for c in ref_id))
        ref = mgr.get_ref(ref_id)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.identity.title, "Track A")
        self.assertEqual(ref.cached_item_key, "ik-100")
        self.assertEqual(ref.roon_session_key, sk)

    def test_mint_ref_stores_item_key_path(self):
        mgr = self._make_mgr()
        ref_id = mgr.mint_ref(
            identity=self._identity(),
            recipe=self._recipe(),
            item_key="ik-leaf",
            session_key="search-1",
            item_key_path=["ik-root", "ik-mid", "ik-leaf"],
        )
        ref = mgr.get_ref(ref_id)
        self.assertEqual(ref.item_key_path, ["ik-root", "ik-mid", "ik-leaf"])

    def test_mint_ref_copies_recipe(self):
        """Recipe stored in ref should be a copy, not a shared reference."""
        mgr = self._make_mgr()
        recipe = self._recipe()
        original_chain = [self._identity("Parent")]
        recipe.parent_chain = original_chain
        ref_id = mgr.mint_ref(self._identity(), recipe, "ik-1", "search-1")
        ref = mgr.get_ref(ref_id)
        # Mutating the original should not affect the stored ref
        original_chain.append(self._identity("Extra"))
        self.assertEqual(len(ref.recipe.parent_chain), 1)

    def test_mint_ref_lru_eviction(self):
        mgr = self._make_mgr(max_refs=3)
        r1 = mgr.mint_ref(self._identity("A"), self._recipe(), "ik-1", "search-1")
        r2 = mgr.mint_ref(self._identity("B"), self._recipe(), "ik-2", "search-1")
        r3 = mgr.mint_ref(self._identity("C"), self._recipe(), "ik-3", "search-1")
        self.assertEqual(len(mgr.refs), 3)

        # Access r1 so it's no longer the oldest
        mgr.get_ref(r1)

        # Adding a 4th should evict r2 (now the oldest accessed)
        r4 = mgr.mint_ref(self._identity("D"), self._recipe(), "ik-4", "search-1")
        self.assertEqual(len(mgr.refs), 3)
        self.assertIsNone(mgr.get_ref(r2))
        self.assertIsNotNone(mgr.get_ref(r1))
        self.assertIsNotNone(mgr.get_ref(r3))
        self.assertIsNotNone(mgr.get_ref(r4))


class TestFindExistingRef(unittest.TestCase):

    def test_find_existing_ref_same_session(self):
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        mgr.mint_ref(
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            item_key="ik-42",
            session_key="search-1",
        )
        found = mgr.find_existing_ref("search-1", "ik-42")
        self.assertIsNotNone(found)
        self.assertEqual(found.identity.title, "Track")

    def test_find_existing_ref_different_session_returns_none(self):
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        mgr.mint_ref(
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            item_key="ik-42",
            session_key="search-1",
        )
        # Different session key — find_existing_ref scopes to session
        found = mgr.find_existing_ref("search-2", "ik-42")
        self.assertIsNone(found)

    def test_find_existing_ref_none_item_key(self):
        mgr = BrowseSessionManager()
        found = mgr.find_existing_ref("search-1", None)
        self.assertIsNone(found)

    def test_find_existing_ref_updates_access_time(self):
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        ref_id = mgr.mint_ref(
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            item_key="ik-1",
            session_key="search-1",
        )
        original_time = mgr.refs[ref_id].last_accessed
        with patch("roon_core.browse_session.time.monotonic", return_value=original_time + 1.0):
            mgr.find_existing_ref("search-1", "ik-1")
        self.assertGreater(mgr.refs[ref_id].last_accessed, original_time)


class TestIsKeyLive(unittest.TestCase):

    def test_is_key_live_true_after_new_session(self):
        """With multi_session_key isolation, keys from earlier sessions remain live."""
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        ref = StableReference(
            ref_id="00001",
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            cached_item_key="ik-1",
            roon_session_key=sk,
        )
        mgr.new_search_session()  # Now active is a different key
        self.assertTrue(mgr.is_key_live(ref))

    def test_is_key_live_false_for_unknown_session(self):
        """Keys from sessions not tracked by the manager are not live."""
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        ref = StableReference(
            ref_id="00001",
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            cached_item_key="ik-1",
            roon_session_key="unknown-session",
        )
        self.assertFalse(mgr.is_key_live(ref))

    def test_is_key_live_false_when_no_cached_key(self):
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        ref = StableReference(
            ref_id="00001",
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            cached_item_key=None,
            roon_session_key="search-1",
        )
        self.assertFalse(mgr.is_key_live(ref))


class TestSessionDepth(unittest.TestCase):

    def test_set_and_get_depth(self):
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        mgr.set_session_depth(sk, 3)
        self.assertEqual(mgr.get_session_depth(sk), 3)

    def test_depth_clamps_to_zero(self):
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        mgr.set_session_depth(sk, -5)
        self.assertEqual(mgr.get_session_depth(sk), 0)

    def test_unknown_session_returns_zero(self):
        mgr = BrowseSessionManager()
        self.assertEqual(mgr.get_session_depth("nonexistent"), 0)

    def test_multiple_sessions_independent_depth(self):
        mgr = BrowseSessionManager()
        sk1 = mgr.new_search_session()
        sk2 = mgr.new_search_session()
        mgr.set_session_depth(sk1, 2)
        mgr.set_session_depth(sk2, 5)
        self.assertEqual(mgr.get_session_depth(sk1), 2)
        self.assertEqual(mgr.get_session_depth(sk2), 5)


class TestUpdateRefKey(unittest.TestCase):

    def test_update_ref_key(self):
        mgr = BrowseSessionManager()
        ref = StableReference(
            ref_id="00001",
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            cached_item_key="old-key",
            roon_session_key="search-1",
            item_key_path=["old-root", "old-key"],
        )
        original_time = ref.last_accessed
        with patch("roon_core.browse_session.time.monotonic", return_value=original_time + 1.0):
            mgr.update_ref_key(ref, "new-key", "recovery", ["new-root", "new-key"])
        self.assertEqual(ref.cached_item_key, "new-key")
        self.assertEqual(ref.roon_session_key, "recovery")
        self.assertEqual(ref.item_key_path, ["new-root", "new-key"])
        self.assertGreater(ref.last_accessed, original_time)

    def test_update_ref_key_without_path(self):
        mgr = BrowseSessionManager()
        ref = StableReference(
            ref_id="00001",
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            cached_item_key="old-key",
            roon_session_key="search-1",
            item_key_path=["old-root", "old-key"],
        )
        mgr.update_ref_key(ref, "new-key", "search-2")
        self.assertEqual(ref.cached_item_key, "new-key")
        self.assertEqual(ref.roon_session_key, "search-2")
        # Path unchanged when not provided
        self.assertEqual(ref.item_key_path, ["old-root", "old-key"])


class TestGetRef(unittest.TestCase):

    def test_get_ref_returns_none_for_missing(self):
        mgr = BrowseSessionManager()
        self.assertIsNone(mgr.get_ref("nonexistent"))

    def test_get_ref_updates_access_time(self):
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        ref_id = mgr.mint_ref(
            identity=ItemIdentity(title="Track"),
            recipe=SearchRecipe(search_string="track"),
            item_key="ik-1",
            session_key="search-1",
        )
        original_time = mgr.refs[ref_id].last_accessed
        with patch("roon_core.browse_session.time.monotonic", return_value=original_time + 1.0):
            mgr.get_ref(ref_id)
        self.assertGreater(mgr.refs[ref_id].last_accessed, original_time)


class TestSessionContention(unittest.TestCase):
    """`acquire`/`release` — copy-on-contention session leasing.

    The fix for parallel sibling drill-downs: an operation reserves the
    session it intends to browse on. If that session is free it gets it as-is
    (the fast path — independent searches and sequential drills are
    unchanged). If another operation already holds it, the contender is
    leased a fresh, distinct session instead, so the two never share one
    Roon browse cursor.
    """

    def test_acquire_uncontended_returns_same_session(self):
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        self.assertEqual(mgr.acquire(sk), sk)

    def test_acquire_contended_returns_distinct_session(self):
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        first = mgr.acquire(sk)        # op1 holds sk
        second = mgr.acquire(sk)       # op2 contends on the same session
        self.assertEqual(first, sk)
        self.assertNotEqual(second, sk)
        # The leased session is a real, tracked session positioned at root.
        self.assertEqual(mgr.get_session_depth(second), 0)

    def test_each_contender_gets_a_distinct_session(self):
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        leased = {mgr.acquire(sk) for _ in range(4)}  # one owner + three contenders
        # All four reservations are distinct sessions.
        self.assertEqual(len(leased), 4)

    def test_release_allows_reuse_without_split(self):
        mgr = BrowseSessionManager()
        sk = mgr.new_search_session()
        mgr.acquire(sk)
        mgr.release(sk)
        # Freed → a later (sequential) operation reuses it, no split.
        self.assertEqual(mgr.acquire(sk), sk)


if __name__ == "__main__":
    unittest.main()
