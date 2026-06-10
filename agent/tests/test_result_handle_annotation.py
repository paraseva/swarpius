"""Tests for result handle annotation in tool output.

Tools embed [Result handle: ...] markers inline via compact_output(output, handles).
These tests verify the exact annotated format for single and multi-handle cases.
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
    RoonSearchToolOutputSchema,
)
from tools.web_search import (  # noqa: E402
    SearXNGSearchTool,
    SearXNGSearchToolConfig,
    WebSearchResultItemSchema,
    WebSearchToolOutputSchema,
)

_search_tool = RoonSearchTool(RoonSearchToolConfig())
_searxng_tool = SearXNGSearchTool(SearXNGSearchToolConfig())


class TestRoonSearchHandleAnnotation(unittest.TestCase):
    """RoonSearchTool.compact_output prepends handle when provided."""

    def test_single_handle_prepended(self):
        output = RoonSearchToolOutputSchema(
            description="Search results for 'Kate Bush'.",
            groups=[RoonCoreResultsGroupSchema(
                group="-",
                items=[
                    RoonCoreItemSummarySchema(
                        title="Kate Bush", reference="5661c",
                        extra_info="9 Albums", group="-",
                    ),
                ],
            )],
        )
        text = _search_tool.compact_output(output, handles=["res_00001"])
        self.assertTrue(text.startswith("[Result handle: res_00001]\n"))
        self.assertIn("(1) [5661c] Kate Bush | 9 Albums", text)

    def test_no_handles_no_annotation(self):
        output = RoonSearchToolOutputSchema(
            description="Results.",
            groups=[RoonCoreResultsGroupSchema(
                group="-",
                items=[RoonCoreItemSummarySchema(
                    title="Track", reference="t1", group="-",
                )],
            )],
        )
        text = _search_tool.compact_output(output)
        self.assertNotIn("[Result handle:", text)
        self.assertTrue(text.startswith("Results. 1 results."))


class TestSearXNGHandleAnnotation(unittest.TestCase):
    """SearXNGSearchTool.compact_output interleaves handles with query groups."""

    def test_two_queries_interleaved(self):
        output = WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    title="Discography", url="https://a.com",
                    content="Albums.", query="kate bush discography",
                ),
                WebSearchResultItemSchema(
                    title="Biography", url="https://b.com",
                    content="Life.", query="kate bush biography",
                ),
            ],
            category="general",
        )
        text = _searxng_tool.compact_output(
            output, handles=["res_00001", "res_00002"],
        )

        self.assertIn("[Result handle: res_00001]", text)
        self.assertIn("[Result handle: res_00002]", text)

        lines = text.split("\n")
        h1 = next(i for i, ln in enumerate(lines) if "res_00001" in ln)
        h2 = next(i for i, ln in enumerate(lines) if "res_00002" in ln)
        disco = next(i for i, ln in enumerate(lines) if "kate bush discography" in ln)
        bio = next(i for i, ln in enumerate(lines) if "kate bush biography" in ln)

        self.assertLess(h1, disco)
        self.assertLess(h2, bio)
        self.assertLess(disco, bio)

    def test_single_query_with_handle(self):
        output = WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    title="Kate Bush", url="https://a.com",
                    content="Singer.", query="kate bush",
                ),
            ],
            category="general",
        )
        text = _searxng_tool.compact_output(output, handles=["res_00001"])
        self.assertIn("[Result handle: res_00001]", text)
        self.assertIn("kate bush", text)

    def test_no_handles_no_annotation(self):
        output = WebSearchToolOutputSchema(
            results=[
                WebSearchResultItemSchema(
                    title="Test", url="https://a.com",
                    content=".", query="test",
                ),
            ],
            category="general",
        )
        text = _searxng_tool.compact_output(output)
        self.assertNotIn("[Result handle:", text)


if __name__ == "__main__":
    unittest.main()
