"""Tests for RuntimeState.store_result_entries — generic result storage.

These test that given ResultStoreEntry objects, the search history and
result store end up in the correct state.  No tool instances needed —
entries are constructed directly.
"""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.runtime.result_store_types import ResultStoreEntry  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402


def _roon_entry(items, description, item_count, session_key=None, is_drill_down=False):
    return ResultStoreEntry(
        items=items,
        description=description,
        item_count=item_count,
        tool_name="roon_search",
        session_key=session_key,
        is_drill_down=is_drill_down,
    )


def _searxng_entry(items, description, item_count):
    return ResultStoreEntry(
        items=items,
        description=description,
        item_count=item_count,
        tool_name="web_search",
    )


class TestSingleEntryStorage(unittest.TestCase):
    """Storing a single entry creates a history entry and result handle."""

    def test_creates_handle_and_history_entry(self):
        runtime = RuntimeState()
        entry = _roon_entry(
            items=[{"group": "-", "items": [{"title": "Track", "reference": "t1"}]}],
            description='"Kate Bush"',
            item_count=1,
            session_key="sess_1",
        )
        handles = runtime.store_result_entries([entry])
        self.assertEqual(len(handles), 1)
        self.assertEqual(handles[0], "res_00001")
        self.assertEqual(len(runtime.search_history), 1)
        self.assertEqual(runtime.search_history[0].result_handle, "res_00001")
        self.assertEqual(runtime.search_history[0].description, '"Kate Bush"')
        self.assertEqual(runtime.search_history[0].item_count, 1)
        self.assertEqual(runtime.search_history[0].session_key, "sess_1")
        self.assertEqual(runtime.search_history[0].tool_name, "roon_search")

    def test_items_stored_in_result_store(self):
        runtime = RuntimeState()
        items = [{"group": "-", "items": [{"title": "A", "reference": "a1"}]}]
        entry = _roon_entry(items=items, description='"test"', item_count=1)
        handles = runtime.store_result_entries([entry])
        self.assertIn(handles[0], runtime.result_store)
        self.assertEqual(runtime.result_store[handles[0]], items)


class TestMultiEntryStorage(unittest.TestCase):
    """Storing multiple entries (SearXNG multi-query) creates one handle per entry."""

    def test_two_entries_create_two_handles(self):
        runtime = RuntimeState()
        entries = [
            _searxng_entry(
                items=[{"title": "A", "url": "a.com", "query": "q1"}],
                description='"query A"',
                item_count=1,
            ),
            _searxng_entry(
                items=[{"title": "B", "url": "b.com", "query": "q2"}],
                description='"query B"',
                item_count=1,
            ),
        ]
        handles = runtime.store_result_entries(entries)
        self.assertEqual(len(handles), 2)
        self.assertEqual(handles[0], "res_00001")
        self.assertEqual(handles[1], "res_00002")
        self.assertEqual(len(runtime.search_history), 2)
        self.assertEqual(runtime.search_history[0].description, '"query A"')
        self.assertEqual(runtime.search_history[1].description, '"query B"')

    def test_handles_are_sequential(self):
        runtime = RuntimeState()
        handles_1 = runtime.store_result_entries([
            _searxng_entry(items=[{"x": 1}], description='"a"', item_count=1),
        ])
        handles_2 = runtime.store_result_entries([
            _searxng_entry(items=[{"x": 2}], description='"b"', item_count=1),
            _searxng_entry(items=[{"x": 3}], description='"c"', item_count=1),
        ])
        self.assertEqual(handles_1, ["res_00001"])
        self.assertEqual(handles_2, ["res_00002", "res_00003"])


class TestDrillDownRouting(unittest.TestCase):
    """Drill-down entries update existing history entries by session key."""

    def test_drill_down_updates_existing_entry(self):
        runtime = RuntimeState()
        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "Albums", "reference": "a1"}]}],
            description='"Kate Bush"',
            item_count=1,
            session_key="sess_1",
        )])
        handle = runtime.search_history[0].result_handle

        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [
                {"title": "Track 1", "reference": "t1"},
                {"title": "Track 2", "reference": "t2"},
            ]}],
            description="drill_down ref a1",
            item_count=2,
            session_key="sess_1",
            is_drill_down=True,
        )])

        # Still 1 history entry
        self.assertEqual(len(runtime.search_history), 1)
        # Item count updated
        self.assertEqual(runtime.search_history[0].item_count, 2)
        # Description preserved (original search string)
        self.assertEqual(runtime.search_history[0].description, '"Kate Bush"')
        # Same handle, updated items
        self.assertEqual(runtime.search_history[0].result_handle, handle)
        self.assertEqual(len(runtime.result_store[handle][0]["items"]), 2)

    def test_drill_down_targets_correct_session(self):
        """With two sessions, drill-down on session A updates A, not B."""
        runtime = RuntimeState()
        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "A", "reference": "a1"}]}],
            description='"Favourites"',
            item_count=1,
            session_key="sess_A",
        )])
        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "B", "reference": "b1"}]}],
            description='"2000s"',
            item_count=1,
            session_key="sess_B",
        )])

        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [
                {"title": "T1", "reference": "t1"},
                {"title": "T2", "reference": "t2"},
            ]}],
            description="drill_down ref a1",
            item_count=2,
            session_key="sess_A",
            is_drill_down=True,
        )])

        self.assertEqual(len(runtime.search_history), 2)
        self.assertEqual(runtime.search_history[0].item_count, 2)  # A updated
        self.assertEqual(runtime.search_history[1].item_count, 1)  # B unchanged

    def test_drill_down_no_session_key_falls_back_to_last(self):
        runtime = RuntimeState()
        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "A", "reference": "a1"}]}],
            description='"test"',
            item_count=1,
        )])

        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [
                {"title": "Sub1", "reference": "s1"},
                {"title": "Sub2", "reference": "s2"},
            ]}],
            description="drill_down ref a1",
            item_count=2,
            is_drill_down=True,
        )])

        self.assertEqual(len(runtime.search_history), 1)
        self.assertEqual(runtime.search_history[0].item_count, 2)

    def test_drill_down_unknown_session_creates_new_entry(self):
        runtime = RuntimeState()
        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "A", "reference": "a1"}]}],
            description='"test"',
            item_count=1,
            session_key="sess_A",
        )])

        # Drill down on unknown session
        runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "X", "reference": "x1"}]}],
            description="drill_down ref x1",
            item_count=1,
            session_key="sess_UNKNOWN",
            is_drill_down=True,
        )])

        self.assertEqual(len(runtime.search_history), 2)
        self.assertEqual(runtime.search_history[1].session_key, "sess_UNKNOWN")

    def test_drill_down_returns_existing_handle(self):
        """Drill-down reuses the existing handle, not a new one."""
        runtime = RuntimeState()
        handles_1 = runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "A", "reference": "a1"}]}],
            description='"test"',
            item_count=1,
            session_key="sess_1",
        )])

        handles_2 = runtime.store_result_entries([_roon_entry(
            items=[{"group": "-", "items": [{"title": "B", "reference": "b1"}]}],
            description="drill_down ref a1",
            item_count=1,
            session_key="sess_1",
            is_drill_down=True,
        )])

        self.assertEqual(handles_1[0], handles_2[0])


class TestEviction(unittest.TestCase):
    """History eviction when max entries exceeded."""

    def test_evicts_oldest_entries(self):
        from app.settings import get_settings
        SEARCH_HISTORY_MAX_ENTRIES = get_settings().search_history_max_entries

        runtime = RuntimeState()
        for i in range(SEARCH_HISTORY_MAX_ENTRIES + 3):
            runtime.store_result_entries([_searxng_entry(
                items=[{"title": f"Result {i}"}],
                description=f'"query {i}"',
                item_count=1,
            )])

        self.assertEqual(len(runtime.search_history), SEARCH_HISTORY_MAX_ENTRIES)
        # Latest entry should be the last one created
        self.assertIn(f"query {SEARCH_HISTORY_MAX_ENTRIES + 2}", runtime.search_history[-1].description)


if __name__ == "__main__":
    unittest.main()
