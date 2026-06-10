"""Tests for roon_search output formatting in LLM conversation context.

Verifies the exact text format that the coordinator sees after a roon_search
tool call.  This format must remain stable — the coordinator's ability to
parse references and item indices depends on it.
"""

import unittest

from roon_core.schemas import RoonCoreItemSummarySchema, RoonCoreResultsGroupSchema
from tools.roon_search import RoonSearchTool, RoonSearchToolConfig, RoonSearchToolOutputSchema

_tool = RoonSearchTool(RoonSearchToolConfig())


def _make_output(description, groups):
    return RoonSearchToolOutputSchema(
        description=description,
        groups=[
            RoonCoreResultsGroupSchema(
                group=g.get("group", "-"),
                items=[
                    RoonCoreItemSummarySchema(**item) for item in g["items"]
                ],
            )
            for g in groups
        ],
    )


class TestRoonSearchContextFormat(unittest.TestCase):
    """Verify the exact compact text format for roon_search in LLM context."""

    def test_single_group_basic_items(self):
        """Standard search result: description header + indexed items."""
        output = _make_output(
            "Search results for 'Kate Bush'.",
            [{
                "group": "-",
                "items": [
                    {"title": "Kate Bush", "reference": "5661c", "extra_info": "9 Albums"},
                    {"title": "Artists", "reference": "381ac", "extra_info": "1 Result"},
                    {"title": "Albums", "reference": "96ba0", "extra_info": "26 Results"},
                ],
            }],
        )
        text = _tool.compact_output(output)
        lines = text.split("\n")
        self.assertEqual(lines[0], "Search results for 'Kate Bush'. 3 results.")
        self.assertEqual(lines[1], "(1) [5661c] Kate Bush | 9 Albums")
        self.assertEqual(lines[2], "(2) [381ac] Artists | 1 Result")
        self.assertEqual(lines[3], "(3) [96ba0] Albums | 26 Results")
        self.assertEqual(len(lines), 4)

    def test_multi_group_items_flattened(self):
        """Multiple groups are flattened with continuous indexing.
        The group wrapper label is not included — only per-item fields."""
        output = _make_output(
            "Search results for 'Beatles'.",
            [
                {
                    "group": "Albums",
                    "items": [
                        {"title": "Abbey Road", "reference": "alb1", "extra_info": "The Beatles"},
                    ],
                },
                {
                    "group": "Tracks",
                    "items": [
                        {"title": "Let It Be", "reference": "trk1", "extra_info": "The Beatles"},
                    ],
                },
            ],
        )
        text = _tool.compact_output(output)
        lines = text.split("\n")
        self.assertEqual(lines[0], "Search results for 'Beatles'. 2 results.")
        self.assertEqual(lines[1], "(1) [alb1] Abbey Road | The Beatles")
        self.assertEqual(lines[2], "(2) [trk1] Let It Be | The Beatles")

    def test_action_items_stripped(self):
        """Action items like 'Play Album' are filtered out."""
        output = _make_output(
            "Drilled into album.",
            [{
                "group": "-",
                "items": [
                    {"title": "Play Album", "reference": "action:0"},
                    {"title": "Track 1", "reference": "t1", "extra_info": "Artist A"},
                    {"title": "Track 2", "reference": "t2", "extra_info": "Artist A"},
                ],
            }],
        )
        text = _tool.compact_output(output)
        lines = text.split("\n")
        self.assertEqual(lines[0], "Drilled into album. 2 results.")
        self.assertEqual(lines[1], "(1) [t1] Track 1 | Artist A")
        self.assertEqual(lines[2], "(2) [t2] Track 2 | Artist A")

    def test_description_trailing_dot_not_doubled(self):
        """Description already ending with '.' should not get a double dot."""
        output = _make_output(
            "Found 1 result.",
            [{"group": "-", "items": [
                {"title": "Album X", "reference": "a1"},
            ]}],
        )
        text = _tool.compact_output(output)
        self.assertTrue(text.startswith("Found 1 result. 1 results."))

    def test_empty_results(self):
        """No items → header only with 0 results."""
        output = _make_output(
            "No results for 'xyzzy'.",
            [{"group": "-", "items": []}],
        )
        text = _tool.compact_output(output)
        self.assertEqual(text, "No results for 'xyzzy'. 0 results.")

    def test_group_label_dash_omitted(self):
        """Group '-' (default) is omitted from the line."""
        output = _make_output(
            "Results.",
            [{"group": "-", "items": [
                {"title": "Item A", "reference": "ref1", "extra_info": "info"},
            ]}],
        )
        text = _tool.compact_output(output)
        self.assertIn("(1) [ref1] Item A | info", text)
        self.assertNotIn(" - ", text.split("\n")[1])

    def test_per_item_group_included_when_meaningful(self):
        """Per-item group field (not the wrapper group) appears in the line
        when it's something other than '-'."""
        output = _make_output(
            "Results.",
            [{"group": "-", "items": [
                {"title": "Miles Davis", "reference": "md1", "extra_info": "5 Albums",
                 "group": "Jazz"},
            ]}],
        )
        text = _tool.compact_output(output)
        self.assertEqual(text.split("\n")[1], "(1) [md1] Miles Davis | Jazz | 5 Albums")

    def test_item_without_extra_info(self):
        """Items with no extra_info omit the trailing pipe section."""
        output = _make_output(
            "Results.",
            [{"group": "-", "items": [
                {"title": "Some Track", "reference": "st1"},
            ]}],
        )
        text = _tool.compact_output(output)
        self.assertEqual(text.split("\n")[1], "(1) [st1] Some Track")

    def test_non_roon_search_returns_json_via_registry(self):
        """Tools without compact_output fall through to JSON via the registry."""
        from app.llm.tool_registry import ToolRegistry
        from tools.roon_status import RoonStatusToolInputSchema, RoonStatusToolOutputSchema

        async def _noop(params):
            pass

        reg = ToolRegistry()
        reg.register("roon_status", "Status", RoonStatusToolInputSchema, _noop)
        output = RoonStatusToolOutputSchema(
            operation="get_zones_status",
            result="Playing: Track X",
        )
        text = reg.compact_output("roon_status", output)
        self.assertIn('"operation"', text)
        self.assertIn('"result"', text)


if __name__ == "__main__":
    unittest.main()
