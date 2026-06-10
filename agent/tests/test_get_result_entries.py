"""Tests for tool get_result_entries methods.

Each tool declares what it wants stored via get_result_entries.
These tests verify the declarations are correct — no RuntimeState needed.
"""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.schemas import RoonCoreItemSummarySchema, RoonCoreResultsGroupSchema  # noqa: E402
from tools.roon_search import (  # noqa: E402
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
    RoonSearchToolOutputSchema,
)
from tools.web_search import (  # noqa: E402
    SearXNGSearchTool,
    SearXNGSearchToolConfig,
    WebSearchResultItemSchema,
    WebSearchToolInputSchema,
    WebSearchToolOutputSchema,
)


def _make_search_output(description, items, session_key=None):
    return RoonSearchToolOutputSchema(
        description=description,
        groups=[RoonCoreResultsGroupSchema(
            group="-",
            items=[
                RoonCoreItemSummarySchema(
                    title=t, reference=r, extra_info=e, group="-",
                )
                for t, r, e in items
            ],
        )],
        session_key=session_key,
    )


class TestRoonSearchGetResultEntries(unittest.TestCase):
    """RoonSearchTool.get_result_entries — one entry per search."""

    def setUp(self):
        self.tool = RoonSearchTool(RoonSearchToolConfig())

    def test_new_search_returns_single_entry(self):
        params = RoonSearchToolInputSchema(
            operation="new_search", search_string="Kate Bush",
        )
        output = _make_search_output(
            "Search results for 'Kate Bush'.",
            [("Kate Bush", "5661c", "9 Albums"), ("Albums", "96ba0", "26 Results")],
            session_key="sess_1",
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.description, '"Kate Bush"')
        self.assertEqual(entry.item_count, 2)
        self.assertEqual(entry.session_key, "sess_1")
        self.assertFalse(entry.is_drill_down)
        self.assertEqual(entry.tool_name, "roon_search")
        self.assertEqual(len(entry.items), 1)  # 1 group

    def test_drill_down_sets_flag(self):
        params = RoonSearchToolInputSchema(
            operation="drill_down_reference", reference="5661c",
        )
        output = _make_search_output(
            "Drilled into Kate Bush.",
            [("Track 1", "t1", ""), ("Track 2", "t2", "")],
            session_key="sess_1",
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertTrue(entry.is_drill_down)
        self.assertEqual(entry.description, "drill_down ref 5661c")
        self.assertEqual(entry.item_count, 2)
        self.assertEqual(entry.session_key, "sess_1")

    def test_empty_groups_returns_none(self):
        params = RoonSearchToolInputSchema(
            operation="new_search", search_string="xyzzy",
        )
        output = RoonSearchToolOutputSchema(
            description="No results.", groups=[],
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertIsNone(entries)

    def test_description_uses_raw_search_string(self):
        params = RoonSearchToolInputSchema(
            operation="new_search", search_string="Sounds of the 80s",
        )
        output = _make_search_output(
            "Search results.", [("Album A", "a1", "")],
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertEqual(entries[0].description, '"Sounds of the 80s"')


class TestSearXNGGetResultEntries(unittest.TestCase):
    """SearXNGSearchTool.get_result_entries — one entry per query group."""

    def setUp(self):
        self.tool = SearXNGSearchTool(SearXNGSearchToolConfig())

    def test_single_query_returns_one_entry(self):
        params = WebSearchToolInputSchema(
            queries=["kate bush"], category="general",
        )
        output = WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    title="Kate Bush", url="https://a.com",
                    content="Singer.", query="kate bush",
                ),
            ],
            category="general",
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].description, '"kate bush"')
        self.assertEqual(entries[0].item_count, 1)
        self.assertEqual(entries[0].tool_name, "web_search")
        self.assertIsNone(entries[0].session_key)
        self.assertFalse(entries[0].is_drill_down)

    def test_multi_query_returns_one_entry_per_query(self):
        params = WebSearchToolInputSchema(
            queries=["kate bush discography", "kate bush biography"],
            category="general",
        )
        output = WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    title="Discog", url="https://a.com",
                    content="Albums.", query="kate bush discography",
                ),
                WebSearchResultItemSchema(
                    title="Bio", url="https://b.com",
                    content="Life.", query="kate bush biography",
                ),
            ],
            category="general",
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].description, '"kate bush discography"')
        self.assertEqual(entries[0].item_count, 1)
        self.assertEqual(entries[1].description, '"kate bush biography"')
        self.assertEqual(entries[1].item_count, 1)

    def test_empty_results_returns_none(self):
        params = WebSearchToolInputSchema(
            queries=["test"], category="general",
        )
        output = WebSearchToolOutputSchema(results=[], category="general")
        entries = self.tool.get_result_entries(params, output)
        self.assertIsNone(entries)

    def test_results_grouped_by_query(self):
        """Multiple results for the same query end up in one entry."""
        params = WebSearchToolInputSchema(
            queries=["test"], category="general",
        )
        output = WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    title="A", url="https://a.com", content=".", query="test",
                ),
                WebSearchResultItemSchema(
                    title="B", url="https://b.com", content=".", query="test",
                ),
            ],
            category="general",
        )
        entries = self.tool.get_result_entries(params, output)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].item_count, 2)
        self.assertEqual(len(entries[0].items), 2)


if __name__ == "__main__":
    unittest.main()
