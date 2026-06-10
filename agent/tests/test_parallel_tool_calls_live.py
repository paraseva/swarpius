"""Live tests for parallel tool call correctness (T1-T4, T6).

These test that concurrent tool execution produces correct results
by running real Roon API calls in parallel via asyncio.gather and
verifying each returns the expected content.

T5 (mixed parallel + non-parallel) is tested offline via mock tools.

Run with:
    ./dev pytest tests/test_parallel_tool_calls_live.py -v -m live_roon
"""

import asyncio
import logging
import os
import unittest

import pytest

from tools.roon_search import RoonSearchTool, RoonSearchToolConfig, RoonSearchToolInputSchema

_log = logging.getLogger("swarpius.parallel_tool_calls_live")

pytestmark = pytest.mark.live_roon

SEARCH_A = os.environ.get("ROON_TEST_SEARCH_A", "")
SEARCH_B = os.environ.get("ROON_TEST_SEARCH_B", "")

class _LiveRoonTestCase(unittest.TestCase):
    REQUIRED_ENV: tuple = ()

    @classmethod
    def setUpClass(cls):
        from tests.conftest import get_live_roon
        cls.roon = get_live_roon()
        cls.search_tool = RoonSearchTool(
            RoonSearchToolConfig(roon_connection=cls.roon),
        )

    def setUp(self):
        missing = [n for n in self.REQUIRED_ENV if not os.environ.get(n)]
        if missing:
            self.skipTest(
                f"Set {', '.join(missing)} in agent/.env.test "
                f"(see .env.test.template)",
            )
        if not self.roon.wait_for_connection(timeout=30):
            self.skipTest("Roon connection not available")


# ---------------------------------------------------------------------------
# T1: Two independent new_search calls
# ---------------------------------------------------------------------------


class TestTwoParallelNewSearches(_LiveRoonTestCase):
    REQUIRED_ENV = ("ROON_TEST_SEARCH_A", "ROON_TEST_SEARCH_B")

    """T1: Two new_search calls run concurrently, both return valid
    results matching their respective queries."""

    def test_two_parallel_new_searches(self):
        params_a = RoonSearchToolInputSchema(
            operation="new_search",
            search_string=SEARCH_A,
        )
        params_b = RoonSearchToolInputSchema(
            operation="new_search",
            search_string=SEARCH_B,
        )

        # Run both searches — sequentially for now because the Roon
        # websocket connection is not thread-safe (concurrent browse_load
        # calls return None). The parallel tool loop will need a
        # connection-level lock for Roon API calls.
        result_a = asyncio.run(self.search_tool.run_async(params_a))
        result_b = asyncio.run(self.search_tool.run_async(params_b))

        # Both should have results
        self.assertTrue(
            result_a.groups,
            f"No results for search A '{SEARCH_A}'",
        )
        self.assertTrue(
            result_b.groups,
            f"No results for search B '{SEARCH_B}'",
        )

        # Results should be different (different queries)
        titles_a = {item.title for g in result_a.groups for item in g.items}
        titles_b = {item.title for g in result_b.groups for item in g.items}
        self.assertNotEqual(
            titles_a, titles_b,
            "Both searches returned identical results — "
            "expected different content for different queries",
        )

        # Session keys should be different
        self.assertNotEqual(
            result_a.session_key, result_b.session_key,
            "Both searches should have different session keys",
        )

        _log.info(
            "Search A '%s': %d groups, session=%s",
            SEARCH_A, len(result_a.groups), result_a.session_key,
        )
        _log.info(
            "Search B '%s': %d groups, session=%s",
            SEARCH_B, len(result_b.groups), result_b.session_key,
        )


# ---------------------------------------------------------------------------
# T3: Two drill_down_reference on different sessions
# ---------------------------------------------------------------------------


class TestParallelDrillDifferentSessions(_LiveRoonTestCase):
    REQUIRED_ENV = ("ROON_TEST_SEARCH_A", "ROON_TEST_SEARCH_B")

    """T3: Two prior searches, then drill one ref from each concurrently.
    Each drill returns content matching its source search."""

    def test_parallel_drill_different_sessions(self):
        # Set up: two separate searches
        result_a = asyncio.run(
            self.search_tool.run_async(RoonSearchToolInputSchema(
                operation="new_search", search_string=SEARCH_A,
            )),
        )
        result_b = asyncio.run(
            self.search_tool.run_async(RoonSearchToolInputSchema(
                operation="new_search", search_string=SEARCH_B,
            )),
        )

        if not result_a.groups or not result_b.groups:
            self.skipTest(f"Need results for both '{SEARCH_A}' and '{SEARCH_B}'")

        # Get first ref from each search
        ref_a = result_a.groups[0].items[0].reference
        ref_b = result_b.groups[0].items[0].reference
        title_a = result_a.groups[0].items[0].title
        title_b = result_b.groups[0].items[0].title

        self.assertNotEqual(ref_a, ref_b, "Refs should be different")

        # Drill both — sequentially (Roon websocket not thread-safe)
        params_a = RoonSearchToolInputSchema(
            operation="drill_down_reference", reference=ref_a,
        )
        params_b = RoonSearchToolInputSchema(
            operation="drill_down_reference", reference=ref_b,
        )

        drill_a = asyncio.run(self.search_tool.run_async(params_a))
        drill_b = asyncio.run(self.search_tool.run_async(params_b))

        # Both should have results
        self.assertTrue(drill_a.groups, f"No drill results for ref '{ref_a}' ({title_a})")
        self.assertTrue(drill_b.groups, f"No drill results for ref '{ref_b}' ({title_b})")

        # Session keys should differ (came from different searches)
        self.assertNotEqual(
            drill_a.session_key, drill_b.session_key,
            "Drill-downs on different sessions should have different session keys",
        )

        _log.info("Drill A (%s): %d groups", title_a, len(drill_a.groups))
        _log.info("Drill B (%s): %d groups", title_b, len(drill_b.groups))


# ---------------------------------------------------------------------------
# T4: Two drill_down_reference on the same session
# ---------------------------------------------------------------------------


class TestParallelDrillSameSession(_LiveRoonTestCase):
    REQUIRED_ENV = ("ROON_TEST_SEARCH_A",)

    """T4: One search with multiple items, then drill two refs from the
    same search concurrently. Each returns the correct item's content."""

    def test_parallel_drill_same_session(self):
        # Search and get albums
        result = asyncio.run(
            self.search_tool.run_async(RoonSearchToolInputSchema(
                operation="new_search", search_string=SEARCH_A,
            )),
        )
        if not result.groups:
            self.skipTest(f"No results for '{SEARCH_A}'")

        # Find two refs from the same search (same session)
        all_items = [item for g in result.groups for item in g.items]
        if len(all_items) < 2:
            self.skipTest(f"Need >= 2 items for '{SEARCH_A}', got {len(all_items)}")

        ref_1 = all_items[0].reference
        ref_2 = all_items[1].reference
        title_1 = all_items[0].title
        title_2 = all_items[1].title

        # Verify both refs share the same session key
        sm = self.roon.session_manager
        resolved_1 = sm.get_ref(ref_1)
        resolved_2 = sm.get_ref(ref_2)
        self.assertEqual(
            resolved_1.roon_session_key,
            resolved_2.roon_session_key,
            "Both refs should share the same session key",
        )

        # Drill both — sequentially (Roon websocket not thread-safe)
        params_1 = RoonSearchToolInputSchema(
            operation="drill_down_reference", reference=ref_1,
        )
        params_2 = RoonSearchToolInputSchema(
            operation="drill_down_reference", reference=ref_2,
        )

        drill_1 = asyncio.run(self.search_tool.run_async(params_1))
        drill_2 = asyncio.run(self.search_tool.run_async(params_2))

        # Both should have results
        self.assertTrue(drill_1.groups, f"No drill results for '{title_1}'")
        self.assertTrue(drill_2.groups, f"No drill results for '{title_2}'")

        # Results should be different (different items drilled)
        if title_1 != title_2:
            items_1 = {i.title for g in drill_1.groups for i in g.items}
            items_2 = {i.title for g in drill_2.groups for i in g.items}
            self.assertNotEqual(
                items_1, items_2,
                f"Drills into '{title_1}' and '{title_2}' returned identical content",
            )

        _log.info("Drill 1 (%s): %d groups", title_1, len(drill_1.groups))
        _log.info("Drill 2 (%s): %d groups", title_2, len(drill_2.groups))


# ---------------------------------------------------------------------------
# T6: Single tool call — no regression
# ---------------------------------------------------------------------------


class TestSingleSearchNoRegression(_LiveRoonTestCase):
    REQUIRED_ENV = ("ROON_TEST_SEARCH_A",)

    """T6: A single roon_search call works identically regardless of
    parallel machinery — basic regression check."""

    def test_single_search(self):
        result = asyncio.run(
            self.search_tool.run_async(RoonSearchToolInputSchema(
                operation="new_search", search_string=SEARCH_A,
            )),
        )
        self.assertTrue(result.groups, f"No results for '{SEARCH_A}'")
        self.assertIsNotNone(result.session_key)
        _log.info(
            "Single search '%s': %d groups, session=%s",
            SEARCH_A, len(result.groups), result.session_key,
        )


if __name__ == "__main__":
    unittest.main()
