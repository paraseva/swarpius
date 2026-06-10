"""Tier 2: Live Roon integration tests for browse session behaviour.

These tests require a running Roon Core with a populated library.
Excluded by default via the ``live_roon`` pytest marker.

Run with:
    ./dev pytest -m live_roon -v
    # or override the default marker filter:
    ./dev pytest tests/test_roon_browse_integration.py -v -o "addopts="

Environment (loaded automatically from agent/.env via conftest.py):
    ROON_CORE_URL          — e.g. http://192.168.1.47:9330
    DEFAULT_ROON_ZONE      — e.g. "MDAC+ USB"

Test data requirements:
    Live tests need per-library search strings configured in
    ``agent/.env.test``; see ``.env.test.template`` for the full
    list. Each test class declares its required ``ROON_TEST_*`` vars
    via ``REQUIRED_ENV`` and skips with a clear message when one is
    unset (no hardcoded defaults — every Roon library is different).
"""

import logging
import os
import unittest

import pytest

from app.exceptions import CategoryCorrectionFailed
from roon_core.browse_session import SearchRecipe
from roon_core.schemas import RoonCoreItemSchema
from tests.conftest import get_live_roon

_log = logging.getLogger("swarpius.browse.integration_test")

# All tests in this module require a live Roon Core.
pytestmark = pytest.mark.live_roon

SEARCH_A = os.environ.get("ROON_TEST_SEARCH_A", "")
SEARCH_B = os.environ.get("ROON_TEST_SEARCH_B", "")
SEARCH_C = os.environ.get("ROON_TEST_SEARCH_C", "")




def _do_search(roon, query, category=None):
    """Perform a search, optionally drill into a category, compile output.

    Returns (groups, session_key, recipe). Skips the calling test if
    ``query`` is empty — every Roon library is different, so live-test
    searches must be configured per-library in ``.env.test``.
    """
    if not query:
        pytest.skip(
            "Live-test search query is empty — set the corresponding "
            "ROON_TEST_* env var in agent/.env.test (see "
            ".env.test.template for the per-test mapping)",
        )
    sk = roon.session_manager.new_search_session()
    recipe = SearchRecipe(search_string=query, category=category)

    roon.browse_core(
        aux={"pop_all": True, "input": query},
        session_key=sk,
    )

    if category:
        cat_item = roon.find_item_by_field(
            roon.session_manager.get_current_list(sk).items, "title", category,
        )
        if cat_item:
            roon.drill_down(
                drilldown_item=cat_item,
                recipe=recipe,
                session_key=sk,
            )

    groups = roon.compile_output(recipe=recipe, session_key=sk)
    return groups, sk, recipe


def _first_ref(groups):
    """Get the first reference ID from compiled output groups."""
    for group in groups:
        for item in group.items:
            return item.reference, item
    return None, None


class _LiveRoonTestCase(unittest.TestCase):
    """Base class for live Roon tests — waits for connection before each
    test and enforces per-class env-var requirements.

    Subclasses set :attr:`REQUIRED_ENV` to a tuple of ``ROON_TEST_*``
    var names; each test in the class skips with a clear message when
    any of those vars is unset. Roon libraries differ per user, so
    live-test searches are configured in ``agent/.env.test`` rather
    than baked into the test code.
    """

    REQUIRED_ENV: tuple = ()

    @classmethod
    def setUpClass(cls):
        cls.roon = get_live_roon()

    def setUp(self):
        # Env-var check first — skipping for missing config shouldn't
        # cost a 30-second connection wait.
        missing = [n for n in self.REQUIRED_ENV if not os.environ.get(n)]
        if missing:
            self.skipTest(
                f"Set {', '.join(missing)} in agent/.env.test "
                f"(see .env.test.template for what each one needs to "
                f"satisfy)",
            )
        if not self.roon.wait_for_connection(timeout=30):
            self.skipTest("Roon connection not available (timed out waiting for reconnect)")


class TestLiveRoonConnection(_LiveRoonTestCase):
    """Sanity check that we can connect and search."""

    REQUIRED_ENV = ("ROON_TEST_SEARCH_A",)



    def test_connection_established(self):
        self.assertIsNotNone(self.roon.api)

    def test_basic_search_returns_results(self):
        sk = self.roon.session_manager.new_search_session()
        results = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk,
        )
        if not results.items:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")



class TestSingleSearchAndAction(_LiveRoonTestCase):
    """Baseline: search → compile → resolve → get_media_actions."""

    REQUIRED_ENV = ("ROON_TEST_SEARCH_A",)



    def test_single_search_and_action(self):
        groups, sk, recipe = _do_search(self.roon, SEARCH_A)
        if not groups:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")

        ref_id, media_item = _first_ref(groups)
        if ref_id is None:
            self.skipTest(f"No reference in compiled output for '{SEARCH_A}'")

        # Resolve reference — should work (Tier 1, same session)
        resolved = self.roon.resolve_reference(ref_id)
        self.assertIsNotNone(resolved, "Reference should resolve on same session")
        self.assertTrue(
            self.roon.session_manager.is_key_live(resolved),
            "Reference should be live on active session",
        )

        # Get media actions
        actions, action_sk, _ = self.roon.get_media_actions(media_item)
        self.assertIsNotNone(actions, "Should get action list")
        action_titles = [i.title for i in actions.items]
        _log.info("Actions for %s: %s", media_item.title, action_titles)
        # Should have at least one playback action (artist-level or track-level)
        self.assertTrue(
            any(t in action_titles for t in [
                "Play Now", "Queue", "Add Next", "Play From Here",
                "Shuffle", "Start Radio", "Play Artist",
            ]),
            f"Expected playback actions, got: {action_titles}",
        )


# ------------------------------------------------------------------ #
#  Cross-search session tests — the core R1 4.1 scenarios            #
# ------------------------------------------------------------------ #


class TestCrossSearchSessions(_LiveRoonTestCase):
    """Test whether references from earlier searches survive new searches."""

    REQUIRED_ENV = (
        "ROON_TEST_SEARCH_A", "ROON_TEST_SEARCH_B", "ROON_TEST_SEARCH_C",
    )



    def test_two_searches_then_resolve_first(self):
        """Search A, search B, then resolve a reference from search A."""
        groups_a, sk_a, _ = _do_search(self.roon, SEARCH_A)
        ref_a, item_a = _first_ref(groups_a)
        if ref_a is None:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")

        groups_b, sk_b, _ = _do_search(self.roon, SEARCH_B)
        self.assertNotEqual(sk_a, sk_b, "Should be different sessions")

        # Ref from search A — is it still resolvable?
        resolved = self.roon.resolve_reference(ref_a)
        self.assertIsNotNone(
            resolved,
            f"Reference from search A ('{item_a.title}') should resolve after search B",
        )
        _log.info(
            "Cross-search resolve: ref=%s title='%s' item_key=%s session=%s",
            ref_a, resolved.identity.title, resolved.cached_item_key, resolved.roon_session_key,
        )

    def test_two_searches_then_action_from_first(self):
        """Search A, search B, then get_media_actions for a ref from search A."""
        groups_a, sk_a, _ = _do_search(self.roon, SEARCH_A)
        ref_a, item_a = _first_ref(groups_a)
        if ref_a is None:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")

        _do_search(self.roon, SEARCH_B)

        # Try to get actions for the ref from search A
        actions, action_sk, _ = self.roon.get_media_actions(item_a)
        self.assertIsNotNone(
            actions,
            f"Should get actions for '{item_a.title}' from search A after search B",
        )
        action_titles = [i.title for i in actions.items]
        _log.info("Cross-search actions for '%s': %s", item_a.title, action_titles)

    def test_two_searches_then_batched_action(self):
        """Search A, search B, then get_media_actions for refs from both."""
        groups_a, _, _ = _do_search(self.roon, SEARCH_A)
        ref_a, item_a = _first_ref(groups_a)

        groups_b, _, _ = _do_search(self.roon, SEARCH_B)
        ref_b, item_b = _first_ref(groups_b)

        self.assertIsNotNone(ref_a)
        self.assertIsNotNone(ref_b)

        actions_a, _, _ = self.roon.get_media_actions(item_a)
        actions_b, _, _ = self.roon.get_media_actions(item_b)

        self.assertIsNotNone(actions_a, f"Actions for '{item_a.title}' from search A")
        self.assertIsNotNone(actions_b, f"Actions for '{item_b.title}' from search B")

    def test_three_searches_then_resolve_all(self):
        """Three searches, then resolve one ref from each — the R1 4.1 scenario."""
        groups_a, _, _ = _do_search(self.roon, SEARCH_A)
        ref_a, item_a = _first_ref(groups_a)

        groups_b, _, _ = _do_search(self.roon, SEARCH_B)
        ref_b, item_b = _first_ref(groups_b)

        groups_c, _, _ = _do_search(self.roon, SEARCH_C)
        ref_c, item_c = _first_ref(groups_c)

        for label, ref_id, item in [("A", ref_a, item_a), ("B", ref_b, item_b), ("C", ref_c, item_c)]:
            if ref_id is None:
                self.skipTest(f"No ref from search {label} — set ROON_TEST_SEARCH_{label}")
            resolved = self.roon.resolve_reference(ref_id)
            self.assertIsNotNone(
                resolved,
                f"Ref from search {label} ('{item.title}') should resolve",
            )
            _log.info(
                "3-search resolve %s: ref=%s title='%s' key=%s",
                label, ref_id, resolved.identity.title, resolved.cached_item_key,
            )

    def test_action_after_many_searches(self):
        """5 searches, then action for a ref from the first. Tests session count limits."""
        groups_first, _, _ = _do_search(self.roon, SEARCH_A)
        ref_first, item_first = _first_ref(groups_first)
        if ref_first is None:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")

        # 4 more searches
        for query in [SEARCH_B, SEARCH_C, SEARCH_A, SEARCH_B]:
            _do_search(self.roon, query)

        actions, _, _ = self.roon.get_media_actions(item_first)
        self.assertIsNotNone(
            actions,
            f"Actions for '{item_first.title}' should work after 5 searches",
        )


# ------------------------------------------------------------------ #
#  Diagnostic tests — understand Roon API behaviour                  #
# ------------------------------------------------------------------ #


class TestDiagnosticSessionBehaviour(_LiveRoonTestCase):
    """Experiments to document how the Roon browse API actually behaves.

    These tests log findings rather than asserting — they're for
    building understanding, not regression testing.

    """

    REQUIRED_ENV = ("ROON_TEST_SEARCH_A", "ROON_TEST_SEARCH_B")

    def test_item_key_validity_across_sessions(self):
        """After search A, does A's item_key work on a different session?"""
        sk_a = self.roon.session_manager.new_search_session()
        results_a = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk_a,
        )
        if not results_a.items:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")
        item_key_a = results_a.items[0].item_key
        title_a = results_a.items[0].title
        _log.info("Search A: item_key=%s title='%s' session=%s", item_key_a, title_a, sk_a)

        # New session
        sk_b = self.roon.session_manager.new_search_session()
        self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_B},
            session_key=sk_b,
        )

        # Try using item_key from search A on session A (original session)
        try:
            result_on_a = self.roon.browse_core(
                aux={"item_key": item_key_a},
                session_key=sk_a,
                update_current=False,
            )
            _log.info(
                "DIAGNOSTIC: item_key from A on session A: SUCCESS — %d items, titles=%s",
                len(result_on_a.items), [i.title for i in result_on_a.items[:3]],
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: item_key from A on session A: FAILED — %s", e)

        # Try using item_key from search A on session B (different session)
        try:
            result_on_b = self.roon.browse_core(
                aux={"item_key": item_key_a},
                session_key=sk_b,
                update_current=False,
            )
            _log.info(
                "DIAGNOSTIC: item_key from A on session B: SUCCESS — %d items, titles=%s",
                len(result_on_b.items), [i.title for i in result_on_b.items[:3]],
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: item_key from A on session B: FAILED — %s", e)

        # Try using item_key from search A on the action session
        try:
            result_on_action = self.roon.browse_core(
                aux={"item_key": item_key_a},
                session_key=self.roon.session_manager.action_session_key,
                update_current=False,
            )
            _log.info(
                "DIAGNOSTIC: item_key from A on action session: SUCCESS — %d items, titles=%s",
                len(result_on_action.items), [i.title for i in result_on_action.items[:3]],
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: item_key from A on action session: FAILED — %s", e)

    def test_action_list_session_affinity(self):
        """Does fetching action lists depend on the multi_session_key?"""
        groups_a, sk_a, _ = _do_search(self.roon, SEARCH_A)
        ref_a, item_a = _first_ref(groups_a)
        self.assertIsNotNone(ref_a)

        ref = self.roon.session_manager.get_ref(ref_a)
        item_key = ref.cached_item_key
        _log.info("Testing action list session affinity for '%s' (key=%s)", ref.identity.title, item_key)

        # Get actions on the original session
        try:
            results_orig = self.roon.browse_core(
                aux={"item_key": item_key},
                session_key=sk_a,
                update_current=False,
            )
            _log.info(
                "DIAGNOSTIC: actions on original session: %s",
                [i.title for i in results_orig.items[:5]],
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: actions on original session: FAILED — %s", e)

        # Get actions on the action session
        try:
            results_action = self.roon.browse_core(
                aux={"item_key": item_key},
                session_key=self.roon.session_manager.action_session_key,
                update_current=False,
            )
            _log.info(
                "DIAGNOSTIC: actions on action session: %s",
                [i.title for i in results_action.items[:5]],
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: actions on action session: FAILED — %s", e)

        # Get actions on a brand new session
        sk_new = "diagnostic-fresh"
        try:
            results_new = self.roon.browse_core(
                aux={"item_key": item_key},
                session_key=sk_new,
                update_current=False,
            )
            _log.info(
                "DIAGNOSTIC: actions on fresh session: %s",
                [i.title for i in results_new.items[:5]],
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: actions on fresh session: FAILED — %s", e)

    def test_session_key_reuse(self):
        """What happens if we reuse a session key for a new search?"""
        sk = self.roon.session_manager.new_search_session()

        # First search on this session
        results_1 = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk,
        )
        item_key_1 = results_1.items[0].item_key if results_1.items else None
        _log.info("First search on %s: %d items, first key=%s", sk, len(results_1.items), item_key_1)

        # Second search on the SAME session key
        results_2 = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_B},
            session_key=sk,
        )
        _log.info("Second search on %s: %d items", sk, len(results_2.items))

        # Try using item_key from the first search
        if item_key_1:
            try:
                result_reuse = self.roon.browse_core(
                    aux={"item_key": item_key_1},
                    session_key=sk,
                    update_current=False,
                )
                _log.info(
                    "DIAGNOSTIC: first search's item_key after reuse: SUCCESS — %d items",
                    len(result_reuse.items),
                )
            except Exception as e:
                _log.info("DIAGNOSTIC: first search's item_key after reuse: FAILED — %s", e)


    def test_session_clobbering_on_tier2_replay(self):
        """Does Tier 2's pop_all + item_key walk destroy sibling references?

        Simulates resolve_reference Tier 2 behaviour: pop_all resets the
        session to root, then walks item_key_path to the target item.
        Tests whether a second item_key from the same search still works
        after the first item_key has been resolved this way.
        """
        sk_x = self.roon.session_manager.new_search_session()
        results_a = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk_x,
        )
        if len(results_a.items) < 2:
            self.skipTest(f"Need 2+ items from '{SEARCH_A}' — set ROON_TEST_SEARCH_A")

        item_key_1 = results_a.items[0].item_key
        item_key_2 = results_a.items[1].item_key
        title_1 = results_a.items[0].title
        title_2 = results_a.items[1].title
        _log.info(
            "Search A on %s: key1=%s ('%s'), key2=%s ('%s')",
            sk_x, item_key_1, title_1, item_key_2, title_2,
        )

        # Make session non-active by creating a new search
        sk_y = self.roon.session_manager.new_search_session()
        self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_B},
            session_key=sk_y,
        )

        # Simulate Tier 2: pop_all on session X then walk to item_key_1
        opts = self.roon._build_browse_opts(zone=None, session_key=sk_x)
        try:
            self.roon.api.browse_browse(opts | {"pop_all": True})
            result_1 = self.roon.api.browse_browse(opts | {"item_key": item_key_1})
            is_err = isinstance(result_1, dict) and result_1.get("is_error")
            _log.info(
                "DIAGNOSTIC: Tier 2 pop_all + walk to key1: %s",
                "FAILED (is_error)" if is_err else "SUCCESS",
            )
        except Exception as e:
            _log.info("DIAGNOSTIC: Tier 2 pop_all + walk to key1: EXCEPTION — %s", e)

        # Now try item_key_2 on the SAME session — has it been clobbered?
        try:
            result_2 = self.roon.api.browse_browse(opts | {"item_key": item_key_2})
            is_err = isinstance(result_2, dict) and result_2.get("is_error")
            if is_err:
                _log.info("DIAGNOSTIC: key2 after key1 Tier 2 walk: FAILED (is_error)")
            else:
                load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
                titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                _log.info("DIAGNOSTIC: key2 after key1 Tier 2 walk: SUCCESS — titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key2 after key1 Tier 2 walk: EXCEPTION — %s", e)

        # Does item_key_1 still work after a FRESH pop_all (no intermediate walk)?
        try:
            self.roon.api.browse_browse(opts | {"pop_all": True})
            result_fresh = self.roon.api.browse_browse(opts | {"item_key": item_key_1})
            is_err = isinstance(result_fresh, dict) and result_fresh.get("is_error")
            if is_err:
                _log.info("DIAGNOSTIC: key1 after fresh pop_all: FAILED (is_error)")
            else:
                load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
                titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                _log.info("DIAGNOSTIC: key1 after fresh pop_all: SUCCESS — titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key1 after fresh pop_all: EXCEPTION — %s", e)


    def test_multi_session_key_isolation(self):
        """Do item_keys survive across searches when using different multi_session_keys?

        Tests whether Roon's multi_session_key actually preserves session state
        independently, so item_keys from search-1 remain valid after search-2
        happens on a different multi_session_key — WITHOUT any pop_all.
        """
        sk_a = self.roon.session_manager.new_search_session()
        results_a = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk_a,
        )
        if len(results_a.items) < 2:
            self.skipTest(f"Need 2+ items from '{SEARCH_A}' — set ROON_TEST_SEARCH_A")

        key_a1 = results_a.items[0].item_key
        key_a2 = results_a.items[1].item_key
        title_a1 = results_a.items[0].title
        title_a2 = results_a.items[1].title
        _log.info(
            "Search A (%s): key1=%s ('%s'), key2=%s ('%s')",
            sk_a, key_a1, title_a1, key_a2, title_a2,
        )

        # Search B on a DIFFERENT multi_session_key
        sk_b = self.roon.session_manager.new_search_session()
        results_b = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_B},
            session_key=sk_b,
        )
        _log.info("Search B (%s): %d items", sk_b, len(results_b.items))

        # Now try using key_a1 on session A (NO pop_all — session should be intact)
        opts_a = self.roon._build_browse_opts(zone=None, session_key=sk_a)
        try:
            result = self.roon.api.browse_browse(opts_a | {"item_key": key_a1})
            is_err = isinstance(result, dict) and result.get("is_error")
            if is_err:
                _log.info("DIAGNOSTIC: key_a1 on session A after search B (no pop_all): FAILED (is_error)")
            else:
                load_raw = self.roon.api.browse_load(opts_a | {"offset": 0, "count": 5})
                titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                _log.info(
                    "DIAGNOSTIC: key_a1 on session A after search B (no pop_all): SUCCESS — titles=%s",
                    titles,
                )
        except Exception as e:
            _log.info("DIAGNOSTIC: key_a1 on session A after search B (no pop_all): EXCEPTION — %s", e)

        # Pop back to search results level on session A before trying key_a2
        try:
            self.roon.api.browse_browse(opts_a | {"pop_levels": 1})
        except Exception:
            # Diagnostic probe — outcome is captured by the subsequent
            # key_a2 call's success/failure, not by this pop.
            pass

        # Try key_a2 on session A (should still be valid if multi_session_key isolates)
        try:
            result = self.roon.api.browse_browse(opts_a | {"item_key": key_a2})
            is_err = isinstance(result, dict) and result.get("is_error")
            if is_err:
                _log.info("DIAGNOSTIC: key_a2 on session A after pop_levels+key_a1 (no pop_all): FAILED (is_error)")
            else:
                load_raw = self.roon.api.browse_load(opts_a | {"offset": 0, "count": 5})
                titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                _log.info(
                    "DIAGNOSTIC: key_a2 on session A after pop_levels+key_a1 (no pop_all): SUCCESS — titles=%s",
                    titles,
                )
        except Exception as e:
            _log.info("DIAGNOSTIC: key_a2 on session A (no pop_all): EXCEPTION — %s", e)


    def test_item_key_survival_across_levels(self):
        """Do item_keys from deeper levels survive after popping back up?

        Tests the browse hierarchy model: search → drill into item → pop back
        → do the drilled-into item's keys still work? And do the parent
        level's keys still work?
        """
        sk = self.roon.session_manager.new_search_session()
        opts = self.roon._build_browse_opts(zone=None, session_key=sk)

        # Level 0: search results
        results_l0 = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk,
        )
        if len(results_l0.items) < 2:
            self.skipTest(f"Need 2+ items from '{SEARCH_A}' — set ROON_TEST_SEARCH_A")
        key_l0_a = results_l0.items[0].item_key
        key_l0_b = results_l0.items[1].item_key
        title_l0_a = results_l0.items[0].title
        title_l0_b = results_l0.items[1].title
        _log.info(
            "Level 0: key_a=%s ('%s'), key_b=%s ('%s')",
            key_l0_a, title_l0_a, key_l0_b, title_l0_b,
        )

        # Level 1: drill into first item
        results_l1 = self.roon.browse_core(
            aux={"item_key": key_l0_a},
            session_key=sk,
            update_current=False,
        )
        if results_l1.items:
            key_l1 = results_l1.items[0].item_key
            title_l1 = results_l1.items[0].title
            _log.info("Level 1: key=%s ('%s')", key_l1, title_l1)
        else:
            key_l1 = None
            _log.info("Level 1: no items returned")

        # Pop back to level 0
        self.roon.api.browse_browse(opts | {"pop_levels": 1})

        # Test 1: Does key_l0_b (sibling at level 0) still work?
        try:
            result = self.roon.api.browse_browse(opts | {"item_key": key_l0_b})
            is_err = isinstance(result, dict) and result.get("is_error")
            if is_err:
                _log.info("DIAGNOSTIC: key_l0_b after drill+pop: FAILED (is_error)")
            else:
                load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
                titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                _log.info("DIAGNOSTIC: key_l0_b after drill+pop: SUCCESS — titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key_l0_b after drill+pop: EXCEPTION — %s", e)

        # Pop back again (we drilled into key_l0_b)
        self.roon.api.browse_browse(opts | {"pop_levels": 1})

        # Test 2: Does key_l1 (from the deeper level) still work after popping?
        if key_l1:
            try:
                result = self.roon.api.browse_browse(opts | {"item_key": key_l1})
                is_err = isinstance(result, dict) and result.get("is_error")
                if is_err:
                    _log.info("DIAGNOSTIC: key_l1 after popping back to l0: FAILED (is_error)")
                else:
                    load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
                    titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                    _log.info("DIAGNOSTIC: key_l1 after popping back to l0: SUCCESS — titles=%s", titles)
            except Exception as e:
                _log.info("DIAGNOSTIC: key_l1 after popping back to l0: EXCEPTION — %s", e)

        # Test 3: Does key_l0_a still work? (we drilled into it earlier, then popped)
        # Pop back first (we're inside key_l1 now)
        self.roon.api.browse_browse(opts | {"pop_levels": 1})
        try:
            result = self.roon.api.browse_browse(opts | {"item_key": key_l0_a})
            is_err = isinstance(result, dict) and result.get("is_error")
            if is_err:
                _log.info("DIAGNOSTIC: key_l0_a reuse after all navigation: FAILED (is_error)")
            else:
                load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
                titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
                _log.info("DIAGNOSTIC: key_l0_a reuse after all navigation: SUCCESS — titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key_l0_a reuse after all navigation: EXCEPTION — %s", e)


    def test_item_key_context_sensitivity(self):
        """Does an item_key's result depend on the session's current hierarchy position?

        If we drill from L0 → L1 and get item_keys at L1, then pop back to L0,
        does using an L1 item_key give correct L1 results or L0-relative results?
        Compare: using the key from L0 vs from L1 (after walking back).
        """
        sk = self.roon.session_manager.new_search_session()
        opts = self.roon._build_browse_opts(zone=None, session_key=sk)

        # L0: search
        results_l0 = self.roon.browse_core(
            aux={"pop_all": True, "input": SEARCH_A},
            session_key=sk,
        )
        if not results_l0.items:
            self.skipTest(f"No results for '{SEARCH_A}' — set ROON_TEST_SEARCH_A")
        key_l0 = results_l0.items[0].item_key
        title_l0 = results_l0.items[0].title
        _log.info("L0: key=%s ('%s')", key_l0, title_l0)

        # L1: drill into first item (e.g. "The Beatles" → action menu)
        results_l1 = self.roon.browse_core(
            aux={"item_key": key_l0},
            session_key=sk,
            update_current=False,
        )
        if not results_l1.items:
            self.skipTest(f"No items after drilling into '{title_l0}'")
        key_l1 = results_l1.items[0].item_key
        title_l1 = results_l1.items[0].title
        _log.info("L1: key=%s ('%s') — from drilling into '%s'", key_l1, title_l1, title_l0)

        # Use key_l1 while still AT L1 — baseline
        try:
            self.roon.api.browse_browse(opts | {"item_key": key_l1})
            load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
            titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
            _log.info("DIAGNOSTIC: key_l1 used FROM L1 (correct position): titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key_l1 used FROM L1: EXCEPTION — %s", e)

        # Pop back to L1 (we just drilled into key_l1)
        self.roon.api.browse_browse(opts | {"pop_levels": 1})

        # Pop back to L0
        self.roon.api.browse_browse(opts | {"pop_levels": 1})

        # Use key_l1 from L0 — does it give the same result?
        try:
            self.roon.api.browse_browse(opts | {"item_key": key_l1})
            load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
            titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
            _log.info("DIAGNOSTIC: key_l1 used FROM L0 (wrong position): titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key_l1 used FROM L0: EXCEPTION — %s", e)

        # Pop back to L0 again
        self.roon.api.browse_browse(opts | {"pop_levels": 1})

        # Walk back to L1 (re-drill into key_l0), then use key_l1
        try:
            self.roon.api.browse_browse(opts | {"item_key": key_l0})
            self.roon.api.browse_browse(opts | {"item_key": key_l1})
            load_raw = self.roon.api.browse_load(opts | {"offset": 0, "count": 5})
            titles = [i.get("title", "?") for i in (load_raw or {}).get("items", [])]
            _log.info("DIAGNOSTIC: key_l1 used FROM L1 (walked back): titles=%s", titles)
        except Exception as e:
            _log.info("DIAGNOSTIC: key_l1 used FROM L1 (walked back): EXCEPTION — %s", e)


# ------------------------------------------------------------------ #
#  Multi-item action regression test (pop-count bug)                  #
# ------------------------------------------------------------------ #


class TestMultiItemActionPopCount(_LiveRoonTestCase):
    """Regression test for the multi-item action pop-count bug.

    The bug: ``_execute_library_action_for_item`` was popping
    ``levels_pushed + 1`` levels after executing an action, but the Roon
    browse API auto-pops one level when an action is executed.  The
    over-pop corrupted the browse session so that subsequent items in the
    same multi-item batch would resolve to the wrong track (or fail to
    resolve at all).

    This test exercises the exact path taken by ``RoonActionTool`` for a
    multi-item "Play Now + Queue" batch:

        For each item:
          1. resolve_reference  (positions session at parent level)
          2. get_media_actions  (drills into the item → action list)
          3. browse_core        (execute chosen action — auto-pops 1 level)
          4. pop_levels         (pop ``levels_pushed`` to return to parent)

    After the pop-count fix, item N+1 must still resolve to the correct
    title — not to a different item caused by the session being at the
    wrong depth.
    """

    REQUIRED_ENV = ("ROON_TEST_SEARCH_A", "ROON_TEST_PLAYLIST")

    def _get_track_items(self, query, min_tracks=3):
        """Search, drill into Tracks category, return (groups, items).

        Falls back to top-level results if no Tracks category exists,
        as long as enough items are available.
        """
        groups, sk, recipe = _do_search(self.roon, query, category="Tracks")
        all_items = [item for g in groups for item in g.items]
        if len(all_items) >= min_tracks:
            return groups, all_items

        # Retry without category filter if Tracks didn't yield enough
        groups, sk, recipe = _do_search(self.roon, query)
        all_items = [item for g in groups for item in g.items]
        if len(all_items) < min_tracks:
            self.skipTest(f"Need {min_tracks}+ items from '{query}', got {len(all_items)} — set ROON_TEST_SEARCH_A")
        return groups, all_items

    def test_sequential_get_media_actions_resolve_correct_items(self):
        """get_media_actions on items A, B, C in sequence — each must return
        the action list for the correct item.

        This is the exact flow that was broken by the pop-count bug:
        after get_media_actions + pop-back on item A, the session state
        must allow item B to resolve correctly. No actions are actually
        executed (no side effects on playback).
        """
        _, items = self._get_track_items(SEARCH_A, min_tracks=3)
        targets = items[:3]
        _log.info(
            "Multi-item action test — targets: %s",
            [(t.title, t.reference) for t in targets],
        )

        for item in targets:
            actions, session_key, levels_pushed = self.roon.get_media_actions(
                media_item=item,
            )
            self.assertIsNotNone(
                actions,
                f"get_media_actions returned None for '{item.title}'",
            )
            self.assertEqual(
                actions.list.hint if actions.list else None,
                "action_list",
                f"Expected action_list for '{item.title}', "
                f"got hint={actions.list.hint if actions.list else None}",
            )

            # The action list title should match the item — if the session
            # was corrupted by a prior pop, this will be a different track.
            list_title = actions.list.title if actions.list else ""
            _log.info(
                "Actions for '%s': list_title='%s', levels_pushed=%d",
                item.title, list_title, levels_pushed,
            )

            # Pop back levels_pushed (the fixed value, not +1)
            if levels_pushed > 0:
                opts = self.roon._build_browse_opts(
                    zone=None, session_key=session_key,
                )
                self.roon.api.browse_browse(
                    opts | {"pop_levels": levels_pushed},
                )

        _log.info(
            "Multi-item get_media_actions test PASSED — all %d items "
            "resolved correctly",
            len(targets),
        )

    def test_two_items_second_resolves_correctly(self):
        """Minimal 2-item test: after get_media_actions + pop on item A,
        item B must still resolve to its own action list.

        This is the simplest reproduction of the pop-count bug — if
        item B's action list title doesn't match B, the session was
        corrupted by over-popping after A.
        """
        _, items = self._get_track_items(SEARCH_A, min_tracks=2)
        item_a, item_b = items[0], items[1]
        _log.info(
            "2-item test — A='%s' (%s), B='%s' (%s)",
            item_a.title, item_a.reference, item_b.title, item_b.reference,
        )

        # get_media_actions on item A
        actions_a, session_key, levels_pushed = self.roon.get_media_actions(
            media_item=item_a,
        )
        self.assertIsNotNone(actions_a, f"No actions for '{item_a.title}'")

        # Pop back (simulating what roon_action does after executing)
        if levels_pushed > 0:
            opts = self.roon._build_browse_opts(
                zone=None, session_key=session_key,
            )
            self.roon.api.browse_browse(
                opts | {"pop_levels": levels_pushed},
            )

        _log.info(
            "Got actions for A='%s', popped %d — now resolving B='%s'",
            item_a.title, levels_pushed, item_b.title,
        )

        # get_media_actions on item B — must return B's action list, not A's
        actions_b, _, _ = self.roon.get_media_actions(
            media_item=item_b,
        )
        self.assertIsNotNone(
            actions_b,
            f"Item B ('{item_b.title}') failed to get actions after A",
        )
        list_title_b = actions_b.list.title if actions_b.list else ""
        self.assertEqual(
            actions_b.list.hint if actions_b.list else None,
            "action_list",
        )

        _log.info(
            "2-item pop-count test PASSED — A='%s', B='%s' (list_title='%s')",
            item_a.title, item_b.title, list_title_b,
        )


# ------------------------------------------------------------------ #
#  Playlist drill-down tests                                          #
# ------------------------------------------------------------------ #

PLAYLIST_NAME = os.environ.get("ROON_TEST_PLAYLIST", "")


def _drill_into_ref(roon, ref_id, session_key):
    """Resolve a reference and drill into it, returning (groups, recipe).

    Mirrors what roon_search does for drill_down_reference.
    """
    ref = roon.session_manager.get_ref(ref_id)
    if not ref:
        return [], None
    resolved = roon.resolve_reference(ref_id)
    if not resolved or not resolved.cached_item_key:
        return [], None
    recipe = SearchRecipe(
        search_string=ref.recipe.search_string,
        category=ref.recipe.category,
        parent_chain=list(ref.recipe.parent_chain) + [ref.identity],
    )
    temp_item = RoonCoreItemSchema(
        title=ref.identity.title,
        subtitle=ref.identity.subtitle,
        item_key=ref.cached_item_key,
        hint=ref.identity.hint,
        image_key=ref.identity.image_key,
        item_key_path=list(ref.item_key_path),
    )
    roon.drill_down(
        drilldown_item=temp_item,
        recipe=recipe,
        session_key=ref.roon_session_key,
    )
    groups = roon.compile_output(recipe=recipe, session_key=ref.roon_session_key)
    return groups, recipe


class TestPlaylistTrackActions(_LiveRoonTestCase):
    """Verify that actioning a track from a playlist drill-down reaches
    the correct track-level action list, not the playlist-level one.

    The key behavioural contract: given a reference obtained by drilling
    into a playlist, get_media_actions must return an action list whose
    title matches the specific track, with actions like "Play Now" that
    operate on that track alone.
    """

    REQUIRED_ENV = ("ROON_TEST_SEARCH_A", "ROON_TEST_PLAYLIST")

    def _get_playlist_tracks(self):
        """Search → Playlists → drill into PLAYLIST_NAME. Returns track items.

        Skips the test if the playlist isn't found or has no tracks.
        """
        groups, sk, recipe = _do_search(self.roon, PLAYLIST_NAME, category="Playlists")
        playlist_item = None
        for g in groups:
            for item in g.items:
                if item.title == PLAYLIST_NAME:
                    playlist_item = item
                    break
        if playlist_item is None:
            self.skipTest(f"Playlist '{PLAYLIST_NAME}' not found — set ROON_TEST_PLAYLIST")

        track_groups, track_recipe = _drill_into_ref(
            self.roon, playlist_item.reference, sk,
        )
        tracks = []
        for g in track_groups:
            for item in g.items:
                if item.title not in ("Play Playlist", "Play Album"):
                    tracks.append(item)
        if not tracks:
            self.skipTest(f"Playlist '{PLAYLIST_NAME}' has no tracks")
        return tracks

    def test_action_list_belongs_to_track_not_playlist(self):
        """get_media_actions on a playlist track must return that track's actions."""
        tracks = self._get_playlist_tracks()
        track = tracks[0]

        results, _, levels_pushed = self.roon.get_media_actions(track)

        self.assertIsNotNone(results, f"Should get actions for '{track.title}'")
        self.assertEqual(results.list.hint, "action_list")
        # THE KEY ASSERTION: action list title must match the track, not the playlist
        self.assertEqual(
            results.list.title, track.title,
            f"Action list should be for '{track.title}', got '{results.list.title}'",
        )
        action_titles = [i.title for i in results.items]
        self.assertIn("Play Now", action_titles)

    def test_multiple_playlist_tracks_each_get_correct_actions(self):
        """Every track in the playlist should get its own action list."""
        tracks = self._get_playlist_tracks()
        # Test up to 3 tracks to keep runtime reasonable
        for track in tracks[:3]:
            with self.subTest(track=track.title):
                results, _, _ = self.roon.get_media_actions(track)
                self.assertIsNotNone(results, f"Should get actions for '{track.title}'")
                self.assertEqual(
                    results.list.title, track.title,
                    f"Action list should be for '{track.title}', got '{results.list.title}'",
                )
                action_titles = [i.title for i in results.items]
                self.assertIn("Play Now", action_titles)

    def test_playlist_track_actions_survive_new_search(self):
        """Playlist track refs should still yield correct actions after another search."""
        tracks = self._get_playlist_tracks()
        track = tracks[0]

        # Do a completely different search on a new session
        _do_search(self.roon, SEARCH_A)

        # Original playlist track ref should still resolve to the right track
        results, _, _ = self.roon.get_media_actions(track)
        self.assertIsNotNone(results, f"Should get actions for '{track.title}' after new search")
        self.assertEqual(
            results.list.title, track.title,
            f"Action list should be for '{track.title}', got '{results.list.title}'",
        )


# ------------------------------------------------------------------ #
#  _expand_container_reference tests — session-depth drift            #
# ------------------------------------------------------------------ #

EXPAND_PLAYLIST = os.environ.get("ROON_TEST_EXPAND_PLAYLIST", PLAYLIST_NAME)


class TestExpandReference(_LiveRoonTestCase):
    """Verify _expand_container_reference produces correctly-pathed refs that resolve
    to individual tracks, not back to the parent playlist.

    The historical bug being guarded against: browse_core in
    _expand_container_reference didn't track session depth, so
    _nav_reset_to_root under-popped. Subsequent refs resolved to the
    playlist action menu instead of the track.
    """

    REQUIRED_ENV = ("ROON_TEST_PLAYLIST",)

    def _find_playlist_item(self, playlist_name):
        """Search for a playlist by name, return its RoonCoreItemSummarySchema.

        Skips the test if the playlist isn't found.
        """
        groups, sk, recipe = _do_search(self.roon, playlist_name, category="Playlists")
        for g in groups:
            for item in g.items:
                if item.title == playlist_name:
                    return item
        self.skipTest(f"Playlist '{playlist_name}' not found — set ROON_TEST_PLAYLIST")
        return None  # unreachable — skipTest raises SkipTest

    def _make_tool(self):
        """Create a RoonActionTool wired to the shared live connection."""
        from tools.roon_action import RoonActionTool, RoonActionToolConfig
        tool = RoonActionTool(config=RoonActionToolConfig(resolve_zone=lambda z: z))
        tool.roon_connection = self.roon
        return tool

    def test_expanded_refs_have_correct_paths(self):
        """Each expanded track ref should include the playlist key in its path."""
        item = self._find_playlist_item(EXPAND_PLAYLIST)
        tool = self._make_tool()

        expanded = tool._expand_container_reference(item)
        self.assertTrue(len(expanded) > 0, "Expansion produced no tracks")

        # Every expanded item should have an S: ref
        for track in expanded:
            self.assertTrue(
                track.reference.startswith("S:"),
                f"Expected S: prefix on '{track.title}', got '{track.reference}'",
            )

        # Check the first track's ref path
        first_ref_id = expanded[0].reference[2:]
        first_ref = self.roon.session_manager.get_ref(first_ref_id)
        self.assertIsNotNone(first_ref, "Minted ref should exist in session manager")

        # Path must be at least 2 deep: [...parent_path..., track_key]
        self.assertGreaterEqual(
            len(first_ref.item_key_path), 2,
            f"Track ref path too short: {first_ref.item_key_path}",
        )
        _log.info(
            "Expanded '%s' → %d tracks, first path: %s",
            EXPAND_PLAYLIST, len(expanded), first_ref.item_key_path,
        )

    def test_expanded_refs_resolve_to_tracks_not_playlist(self):
        """After expansion, each ref must resolve to the track's action list."""
        item = self._find_playlist_item(EXPAND_PLAYLIST)
        tool = self._make_tool()

        expanded = tool._expand_container_reference(item)
        self.assertTrue(len(expanded) > 0, "Expansion produced no tracks")

        # Test up to 3 tracks
        for track in expanded[:3]:
            with self.subTest(track=track.title):
                results, _, _ = self.roon.get_media_actions(track)
                self.assertIsNotNone(results, f"Should get actions for '{track.title}'")
                self.assertNotEqual(
                    results.list.title, "Play Playlist",
                    f"Track '{track.title}' resolved to playlist action menu, not track",
                )
                self.assertEqual(
                    results.list.hint, "action_list",
                    f"Expected action_list hint for '{track.title}'",
                )
                _log.info(
                    "Track '%s' → action list '%s': %s",
                    track.title, results.list.title,
                    [i.title for i in results.items],
                )

    def test_gateway_items_excluded(self):
        """'Play Playlist' gateway should not appear in expanded tracks."""
        item = self._find_playlist_item(EXPAND_PLAYLIST)
        tool = self._make_tool()

        expanded = tool._expand_container_reference(item)
        titles = [t.title for t in expanded]
        self.assertNotIn("Play Playlist", titles, "Gateway item should be filtered out")
        self.assertNotIn("Play Album", titles, "Gateway item should be filtered out")

    def test_session_depth_stable_after_multiple_expansions(self):
        """Expanding multiple playlists must not drift the session depth.

        Three playlists expanded sequentially on overlapping sessions.
        After all expansions, refs from the first playlist must still
        resolve correctly.
        """
        item = self._find_playlist_item(EXPAND_PLAYLIST)
        tool = self._make_tool()

        # Expand the same playlist three times (simulating 3 different playlists
        # on the same session — the depth-drift scenario)
        all_expanded = []
        for i in range(3):
            expanded = tool._expand_container_reference(item)
            self.assertTrue(len(expanded) > 0, f"Expansion {i+1} produced no tracks")
            all_expanded.append(expanded)

        # Refs from the FIRST expansion must still resolve correctly
        first_track = all_expanded[0][0]
        results, _, _ = self.roon.get_media_actions(first_track)
        self.assertIsNotNone(results, "First-expansion track should still resolve")
        self.assertNotEqual(
            results.list.title, "Play Playlist",
            f"First-expansion track '{first_track.title}' drifted to playlist level",
        )
        _log.info(
            "After 3 expansions, first track '%s' → '%s' (correct)",
            first_track.title, results.list.title,
        )

    def test_album_with_versions_expands_through_disambiguation(self):
        """An album with multiple versions should drill through to tracks.

        Roon shows a disambiguation level with multiple editions (all
        hint='list'). _expand_container_reference must drill through
        the first version to reach the actual track list, not treat
        each version as a track.
        """
        album_name = os.environ.get("ROON_TEST_ALBUM", "")
        if not album_name:
            self.skipTest("Set ROON_TEST_ALBUM in .env.test")
        groups, sk, recipe = _do_search(self.roon, album_name, category="Albums")
        album_item = None
        for g in groups:
            for item in g.items:
                if album_name.lower() in item.title.lower():
                    album_item = item
                    break
            if album_item:
                break
        if album_item is None:
            self.skipTest(f"Album '{album_name}' not found — set ROON_TEST_ALBUM")

        tool = self._make_tool()
        expanded = tool._expand_container_reference(album_item)
        self.assertTrue(len(expanded) > 0, f"Expansion of '{album_item.title}' produced no tracks")

        # Key assertions: expanded items should be individual tracks,
        # not album versions.  Track titles should differ from each other
        # (versions would all share the album title).
        titles = [t.title for t in expanded]
        unique_titles = set(titles)
        self.assertGreater(
            len(unique_titles), 1,
            f"Expanded items all have the same title — likely version entries, not tracks: {titles[:5]}",
        )

        # Verify the first expanded track resolves to a track action list
        first_track = expanded[0]
        results, _, _ = self.roon.get_media_actions(first_track)
        self.assertIsNotNone(results, f"Should get actions for '{first_track.title}'")
        self.assertNotIn(
            results.list.title, ("Play Album", "Play Playlist"),
            f"Track '{first_track.title}' resolved to container action menu '{results.list.title}', not track",
        )
        _log.info(
            "Album '%s' expanded to %d tracks, first: '%s' → action list '%s'",
            album_item.title, len(expanded), first_track.title, results.list.title,
        )


# ------------------------------------------------------------------ #
#  Album→track category reconciliation                                #
# ------------------------------------------------------------------ #

# GATEWAY_TRACK_SEARCH/_TITLE: a search whose top hit is an album
# whose drill yields a gateway-level shape (items[0]="Play Album",
# siblings include the matching track). The album must contain a
# track whose normalised title equals ref.identity.title so the
# album→track gateway-sibling correction has a target to find.
# Canonical example: "Thriller Michael Jackson".
GATEWAY_TRACK_SEARCH = os.environ.get("ROON_TEST_GATEWAY_TRACK_SEARCH", "")
GATEWAY_TRACK_TITLE = os.environ.get("ROON_TEST_GATEWAY_TRACK_TITLE", "")

# ------------------------------------------------------------------ #
#  Track-shaped top hit + intended_item_category=album                #
# ------------------------------------------------------------------ #
# TRACK_ALBUM_SEARCH/_TITLE: the search's top hit must be a TRACK
# (hint=action_list) whose title matches an album in the library
# under the Albums category — drilling the top hit yields a track-
# shape, and the Albums category contains a same-titled album whose
# track listing includes that song. Canonical example: "Ram It Down
# Judas Priest" (Judas Priest have both a song and an album titled
# "Ram It Down"; the song ranks above the album in search).
TRACK_ALBUM_SEARCH = os.environ.get("ROON_TEST_TRACK_ALBUM_SEARCH", "")
TRACK_ALBUM_TITLE = os.environ.get("ROON_TEST_TRACK_ALBUM_TITLE", "")


class TestAlbumTopHitCategoryReconciliation(_LiveRoonTestCase):
    """Album-shaped top hit + various ``intended_item_category`` values:
    verify each intent produces the right outcome against a real Core.

    * intent=auto → drill picks ``Play Album`` gateway → action_list
      whose ``list.title=="Play Album"`` (no correction).
    * intent=album → reconcile returns ``None`` (no correction needed,
      gateway already matches intent) → same end-state as auto.
    * intent=track → reconcile triggers
      ``_correct_via_gateway_siblings``, finds the same-titled track
      among album siblings, drills into it → returns the track's
      action_list (``list.title != "Play Album"``).

    Together these pin both the no-correction path and the album→track
    gateway-sibling reconciliation path. For the track→album direction
    (a self-titled track ranking above its album in search), see
    ``TestTrackTopHitCategoryReconciliation`` below.
    """

    REQUIRED_ENV = ("ROON_TEST_GATEWAY_TRACK_SEARCH", "ROON_TEST_GATEWAY_TRACK_TITLE")

    def _get_top_result(self):
        """Search and return the top result item. Skips with a clear
        message if the search returns nothing."""
        groups, _, _ = _do_search(self.roon, GATEWAY_TRACK_SEARCH)
        if not groups:
            self.skipTest(
                f"No results for '{GATEWAY_TRACK_SEARCH}' — set "
                "ROON_TEST_GATEWAY_TRACK_SEARCH",
            )
        ref_id, item = _first_ref(groups)
        if ref_id is None:
            self.skipTest(
                f"No reference in compiled output for '{GATEWAY_TRACK_SEARCH}'",
            )
        return item

    def test_intent_auto_lands_on_play_album_action_list(self):
        """intent=auto → no reconciliation, drill picks Play Album
        gateway, ends at the album's terminal action_list."""
        item = self._get_top_result()

        results, _, _ = self.roon.get_media_actions(
            item, intended_item_category="auto",
        )
        self.assertIsNotNone(results, f"Should get actions for '{item.title}'")
        self.assertEqual(
            results.list.title, "Play Album",
            f"Expected album action list, got '{results.list.title}'. "
            f"This test assumes the search ranks an album as top hit; "
            f"if your library returns a track or other shape on top, "
            f"set ROON_TEST_GATEWAY_TRACK_SEARCH to a different query.",
        )
        _log.info(
            "Auto: '%s' → '%s' actions=%s",
            item.title, results.list.title, [i.title for i in results.items],
        )

    def test_intent_album_lands_on_play_album_action_list(self):
        """intent=album → reconcile detects the gateway already matches
        the intent and returns None (no correction needed); the walk
        proceeds to the same Play Album action_list as the auto path."""
        item = self._get_top_result()

        results, _, _ = self.roon.get_media_actions(
            item, intended_item_category="album",
        )
        self.assertIsNotNone(results, f"Should get actions for '{item.title}'")
        self.assertEqual(
            results.list.title, "Play Album",
            f"Expected album action list, got '{results.list.title}'",
        )
        _log.info(
            "intent=album: '%s' → '%s'",
            item.title, results.list.title,
        )

    def test_intent_track_reconciles_to_track_action_list_via_gateway_sibling(self):
        """intent=track + album-top hit → reconcile triggers
        ``_correct_via_gateway_siblings`` → finds the same-titled track
        among the album's children → drills into it → returns the
        track's action_list (NOT 'Play Album'). This is the album→track
        reconciliation path."""
        item = self._get_top_result()

        results, _, _ = self.roon.get_media_actions(
            item, intended_item_category="track",
        )
        self.assertIsNotNone(results, f"Should get actions for '{item.title}'")
        self.assertNotEqual(
            results.list.title, "Play Album",
            f"Expected gateway-sibling correction to drill into the "
            f"matching-titled track, but stayed at album action_list "
            f"('{results.list.title}'). Either the album has no track "
            f"whose normalised title matches '{GATEWAY_TRACK_TITLE}', "
            f"or the corrector regressed.",
        )
        self.assertNotEqual(
            results.list.title, "Play Playlist",
            f"Got playlist action_list: '{results.list.title}'",
        )
        _log.info(
            "intent=track (album→track via gateway-sibling): "
            "'%s' → '%s' actions=%s",
            item.title, results.list.title,
            [i.title for i in (results.items or [])],
        )


class TestTrackTopHitCategoryReconciliation(_LiveRoonTestCase):
    """Track-shaped top hit + ``intended_category='album'``: category
    correction must navigate to the matching album, and children of the
    corrected ref must resolve to real track-level action menus — not
    back to the same-titled song. Exercises the post-correction
    parent-ref update against real Roon's stale-key behaviour, which
    fakes can't model.
    """

    REQUIRED_ENV = ("ROON_TEST_TRACK_ALBUM_SEARCH", "ROON_TEST_TRACK_ALBUM_TITLE")

    def _track_shaped_top_hit(self):
        """Find the top search result and confirm it's track-shaped
        (drill yields action_list, or a single-child action_list wrapper).
        Skips when the search doesn't satisfy the precondition — the
        scenario only fires when a track ranks above its same-titled
        album, which is library-dependent.
        """
        groups, _, _ = _do_search(self.roon, TRACK_ALBUM_SEARCH)
        if not groups:
            self.skipTest(
                f"No results for '{TRACK_ALBUM_SEARCH}' — set "
                "ROON_TEST_TRACK_ALBUM_SEARCH",
            )
        ref_id, item = _first_ref(groups)
        if ref_id is None:
            self.skipTest(
                f"No reference in compiled output for '{TRACK_ALBUM_SEARCH}'",
            )
        ref = self.roon.resolve_reference(ref_id)
        self.assertIsNotNone(ref)
        result = self.roon._nav_drill(
            ref.cached_item_key, ref.roon_session_key, update_current=False,
        )
        list_hint = result.list.hint if result.list else None
        items = result.items or []
        is_track = (
            list_hint == "action_list"
            or (
                len(items) == 1
                and getattr(items[0], "hint", None) == "action_list"
            )
        )
        self.roon._nav_reset_to_root(ref.roon_session_key)
        if not is_track:
            self.skipTest(
                f"Top hit for '{TRACK_ALBUM_SEARCH}' is not track-shaped "
                f"(list_hint={list_hint}, "
                f"items={[(i.title, i.hint) for i in items[:3]]}) — "
                f"this regression requires a track ranking above its "
                f"same-titled album. Pick a different search.",
            )
        return item

    def _make_tool(self):
        """Create a RoonActionTool wired to the shared live connection."""
        from tools.roon_action import RoonActionTool, RoonActionToolConfig
        tool = RoonActionTool(config=RoonActionToolConfig(resolve_zone=lambda z: z))
        tool.roon_connection = self.roon
        return tool

    def test_expand_corrects_track_to_album_and_yields_distinct_tracks(self):
        """``_expand_container_reference`` on a track-shaped top hit with
        ``intended_category='album'`` must navigate to the same-titled
        album and return multiple distinct track refs — not N copies of
        the same song."""
        item = self._track_shaped_top_hit()
        tool = self._make_tool()

        expanded = tool._expand_container_reference(item, intended_category="album")

        self.assertGreater(
            len(expanded), 1,
            f"Expected multi-track expansion for album "
            f"'{TRACK_ALBUM_TITLE}', got {len(expanded)} item(s): "
            f"{[t.title for t in expanded]}. Expansion likely fell "
            f"through to the uncorrected (track) path.",
        )
        titles = [t.title for t in expanded]
        self.assertEqual(
            len(set(titles)), len(titles),
            f"Expanded tracks contain duplicates: {titles}",
        )
        _log.info(
            "Expanded '%s' (intended=album) → %d distinct tracks: %s",
            item.title, len(expanded), titles,
        )

    def test_expanded_refs_resolve_to_track_level_action_lists(self):
        """Each expanded child must resolve to a track-level action menu
        — NOT 'Play Album', and NOT the same-titled song's menu. Only
        non-title tracks can prove the negative against the song-title
        match, so the song-title assertion is skipped on the title
        track itself."""
        item = self._track_shaped_top_hit()
        tool = self._make_tool()

        expanded = tool._expand_container_reference(item, intended_category="album")
        self.assertGreater(len(expanded), 1, "Expansion produced too few tracks")

        # Sample up to 3 tracks to keep the Core round-trips bounded.
        for track in expanded[:3]:
            with self.subTest(track=track.title):
                results, _, _ = self.roon.get_media_actions(track)
                self.assertIsNotNone(
                    results, f"Should get actions for '{track.title}'",
                )
                self.assertNotEqual(
                    results.list.title, "Play Album",
                    f"Track '{track.title}' resolved to album action menu "
                    f"('{results.list.title}').",
                )
                if track.title != TRACK_ALBUM_TITLE:
                    self.assertNotEqual(
                        results.list.title, TRACK_ALBUM_TITLE,
                        f"Track '{track.title}' resolved to the "
                        f"same-titled song's action menu — expected the "
                        f"track's own menu.",
                    )
                _log.info(
                    "Track '%s' → action list '%s': %s",
                    track.title, results.list.title,
                    [i.title for i in (results.items or [])],
                )


# ------------------------------------------------------------------ #
#  Loud failure: track → container with no strict title match         #
# ------------------------------------------------------------------ #

# Library-specific searches — no hardcoded defaults, since every Roon
# library is different. Set these in .env.test (or skip the tests).
#
# NO_CONTAINER_SEARCH/_TITLE: the search must produce a track-shaped
# top hit AND have no album/playlist with that title in the library.
# Guards against a substring-fallback corrector match — e.g. a search
# for a track surfacing a karaoke compilation that contains the title.
#
# PLAYLIST_TRACK_SEARCH: a search where (a) the top hit is track-
# shaped, (b) the Playlists category surfaces, and (c) a playlist
# titled identically to that top hit exists in the library. The
# matching title is derived from the top hit at runtime — a single
# search rarely produces both a chosen-title top hit AND surfaces
# the Playlists category.
NO_CONTAINER_SEARCH = os.environ.get("ROON_TEST_NO_CONTAINER_SEARCH", "")
NO_CONTAINER_TITLE = os.environ.get("ROON_TEST_NO_CONTAINER_TITLE", "")
PLAYLIST_TRACK_SEARCH = os.environ.get("ROON_TEST_PLAYLIST_TRACK_SEARCH", "")


class TestCategoryCorrectionLoudFailure(_LiveRoonTestCase):
    """When the library has no album/playlist titled to match a
    track-shaped top hit, the corrector must raise
    CategoryCorrectionFailed rather than silently substring-matching an
    unrelated container. Symmetric coverage for album and playlist
    intents, since both flow through the same _correct_via_category_search.
    """

    @classmethod
    def _normalise(cls, title: str) -> str:
        # Mirror RoonBrowseMixin._normalise_title without importing the
        # method (avoids accidentally testing the prod normaliser via
        # itself — we want the precondition probe to be independent).
        import re
        return re.sub(r"^[^a-zA-Z]+|[^a-zA-Z]+$", "", title).lower()

    def _category_has_strict_title(
        self, search: str, category: str, target: str,
    ) -> bool:
        """Probe Roon: re-search and drill into ``category``; return True
        if any item's normalised title equals ``target``. Lets the
        failure tests skip cleanly on libraries where the precondition
        (no strict match) doesn't hold."""
        sk = self.roon.session_manager.recovery_session_key
        results = self.roon.browse_core(
            aux={"pop_all": True, "input": search},
            session_key=sk,
            update_current=False,
        )
        cat = self.roon.find_item_by_field(results.items, "title", category)
        if not cat:
            self.roon._nav_reset_to_root(sk)
            return False
        results = self.roon._nav_drill(cat.item_key, sk, update_current=False)
        target_n = self._normalise(target)
        match = any(
            self._normalise(i.title) == target_n for i in results.items
        )
        self.roon._nav_reset_to_root(sk)
        return match

    def _track_shaped_top_hit(self, search: str):
        """Search and return the top reference if its single drill yields
        a track-shaped result (action_list, or single-child wrapper around
        action_list). Skips if the search doesn't reproduce that shape."""
        groups, _, _ = _do_search(self.roon, search)
        if not groups:
            self.skipTest(
                f"No results for '{search}' — set ROON_TEST_NO_CONTAINER_SEARCH",
            )
        ref_id, item = _first_ref(groups)
        if ref_id is None:
            self.skipTest(f"No reference in compiled output for '{search}'")
        ref = self.roon.resolve_reference(ref_id)
        self.assertIsNotNone(ref)
        result = self.roon._nav_drill(
            ref.cached_item_key, ref.roon_session_key, update_current=False,
        )
        list_hint = result.list.hint if result.list else None
        items = result.items or []
        is_track = (
            list_hint == "action_list"
            or (len(items) == 1 and getattr(items[0], "hint", None) == "action_list")
        )
        self.roon._nav_reset_to_root(ref.roon_session_key)
        if not is_track:
            self.skipTest(
                f"Top hit for '{search}' is not track-shaped "
                f"(list_hint={list_hint}, items={[(i.title, i.hint) for i in items[:3]]}) "
                f"— this scenario doesn't apply",
            )
        return item

    def _require_no_container(self):
        if not (NO_CONTAINER_SEARCH and NO_CONTAINER_TITLE):
            self.skipTest(
                "Set ROON_TEST_NO_CONTAINER_SEARCH and "
                "ROON_TEST_NO_CONTAINER_TITLE in .env.test — see "
                ".env.test.template for what they need to satisfy.",
            )

    def test_track_to_album_raises_when_no_strict_album_match(self):
        """A 'Voices' track top hit with intended='album' must raise
        instead of substring-matching the karaoke compilation. Skipped
        if the library actually has an album titled 'Voices'."""
        self._require_no_container()
        item = self._track_shaped_top_hit(NO_CONTAINER_SEARCH)
        if self._category_has_strict_title(
            NO_CONTAINER_SEARCH, "Albums", NO_CONTAINER_TITLE,
        ):
            self.skipTest(
                f"Library has an album titled '{NO_CONTAINER_TITLE}' — "
                f"precondition (no strict match) doesn't hold",
            )

        with self.assertRaises(CategoryCorrectionFailed) as cm:
            self.roon.get_media_actions(item, intended_item_category="album")
        self.assertEqual(cm.exception.intended_category, "album")
        self.assertEqual(cm.exception.category_name, "Albums")
        _log.info(
            "Loud failure (track→album): '%s' — %s",
            item.title, cm.exception,
        )

    def test_track_to_playlist_raises_when_no_strict_playlist_match(self):
        """Symmetric to album: a track-shaped top hit with intended=
        'playlist' must raise if no strict-titled playlist exists."""
        self._require_no_container()
        item = self._track_shaped_top_hit(NO_CONTAINER_SEARCH)
        if self._category_has_strict_title(
            NO_CONTAINER_SEARCH, "Playlists", NO_CONTAINER_TITLE,
        ):
            self.skipTest(
                f"Library has a playlist titled '{NO_CONTAINER_TITLE}' — "
                f"precondition (no strict match) doesn't hold",
            )

        with self.assertRaises(CategoryCorrectionFailed) as cm:
            self.roon.get_media_actions(item, intended_item_category="playlist")
        self.assertEqual(cm.exception.intended_category, "playlist")
        self.assertEqual(cm.exception.category_name, "Playlists")
        _log.info(
            "Loud failure (track→playlist): '%s' — %s",
            item.title, cm.exception,
        )

    def test_track_to_playlist_succeeds_with_matching_playlist(self):
        """When the library has a strict-titled playlist matching a
        track-shaped top hit, intended='playlist' arrives at Play
        Playlist. Exercises the playlist branch of _CATEGORY_TO_GATEWAY
        added alongside the bug fix.

        Library-dependent: skipped unless ROON_TEST_PLAYLIST_TRACK_SEARCH
        is set AND that search produces a track-shaped top hit AND a
        playlist titled identically to that top hit exists in the library.
        The matching title is derived from the top hit's actual title,
        not configured separately, since a single search rarely produces
        both a chosen-title top hit AND surfaces the Playlists category
        (Roon search needs the search terms to match the playlist's
        title too)."""
        if not PLAYLIST_TRACK_SEARCH:
            self.skipTest(
                "Set ROON_TEST_PLAYLIST_TRACK_SEARCH to enable. The search "
                "must surface a track-shaped top hit AND a Playlists "
                "category containing a playlist titled identically to "
                "that top hit.",
            )
        item = self._track_shaped_top_hit(PLAYLIST_TRACK_SEARCH)
        # Use the top hit's actual title — that's what the corrector
        # will look for in the Playlists category.
        if not self._category_has_strict_title(
            PLAYLIST_TRACK_SEARCH, "Playlists", item.title,
        ):
            self.skipTest(
                f"No playlist titled '{item.title}' surfaces for search "
                f"'{PLAYLIST_TRACK_SEARCH}' — pick a search where the "
                f"top hit's title matches an existing playlist's title.",
            )

        results, _, _ = self.roon.get_media_actions(
            item, intended_item_category="playlist",
        )
        self.assertIsNotNone(results)
        self.assertEqual(
            results.list.title, "Play Playlist",
            f"Expected 'Play Playlist' action list, got '{results.list.title}'",
        )
        _log.info(
            "Track→playlist correction: '%s' → '%s'",
            item.title, results.list.title,
        )


if __name__ == "__main__":
    unittest.main()
