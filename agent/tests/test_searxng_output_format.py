"""Tests for SearXNG search result formatting in coordinator context."""

import unittest

from tools.web_search import (
    SearXNGSearchTool,
    SearXNGSearchToolConfig,
    WebSearchResultItemSchema,
    WebSearchToolOutputSchema,
)

_tool = SearXNGSearchTool(SearXNGSearchToolConfig())


def _make_output(
    results: list[dict], category: str = "general",
) -> WebSearchToolOutputSchema:
    items = [WebSearchResultItemSchema(**r) for r in results]
    return WebSearchToolOutputSchema(results=items, category=category)


class TestSearXNGOutputFormat(unittest.TestCase):
    """Verify SearXNG results are formatted with aligned field labels."""

    def test_header_contains_query_and_category(self):
        output = _make_output([
            {"title": "Example", "url": "https://example.com", "content": "A page.", "query": "test query"},
        ])
        text = _tool.compact_output(output)
        first_line = text.split("\n")[0]
        assert "test query" in first_line
        assert "(category: general)" in first_line
        assert "1 results" in first_line

    def test_fields_are_aligned(self):
        output = _make_output([
            {"title": "My Title", "url": "https://example.com", "content": "Some content.", "query": "q"},
        ])
        text = _tool.compact_output(output)
        lines = text.split("\n")
        # Find the title/url/content lines
        title_line = next(ln for ln in lines if "title:" in ln)
        url_line = next(ln for ln in lines if "url:" in ln)
        content_line = next(ln for ln in lines if "content:" in ln)
        # Values should start at the same column
        title_val_pos = title_line.index("My Title")
        url_val_pos = url_line.index("https://example.com")
        content_val_pos = content_line.index("Some content.")
        assert title_val_pos == url_val_pos == content_val_pos

    def test_multiple_results_numbered(self):
        output = _make_output([
            {"title": "First", "url": "https://first.com", "content": "One.", "query": "q"},
            {"title": "Second", "url": "https://second.com", "content": "Two.", "query": "q"},
            {"title": "Third", "url": "https://third.com", "content": "Three.", "query": "q"},
        ])
        text = _tool.compact_output(output)
        assert "(1) title:" in text
        assert "(2) title:" in text
        assert "(3) title:" in text
        assert "3 results" in text.split("\n")[0]

    def test_empty_content_omitted(self):
        output = _make_output([
            {"title": "No Content", "url": "https://example.com", "content": None, "query": "q"},
        ])
        text = _tool.compact_output(output)
        assert "content:" not in text

    def test_content_wraps_with_aligned_continuation(self):
        long_content = "word " * 30  # ~150 chars
        output = _make_output([
            {"title": "Long", "url": "https://example.com", "content": long_content.strip(), "query": "q"},
        ])
        text = _tool.compact_output(output)
        content_lines = [ln for ln in text.split("\n") if "content:" in ln or (ln.startswith("             ") and ln.strip())]
        # Should have wrapped to multiple lines
        assert len(content_lines) > 1
        # Continuation lines should be indented to same column as first value
        first = content_lines[0]
        val_start = first.index("word")
        for continuation in content_lines[1:]:
            # Continuation should start with spaces then text at same column
            stripped = continuation.lstrip()
            indent_len = len(continuation) - len(stripped)
            assert indent_len == val_start, f"Expected indent {val_start}, got {indent_len}"

    def test_query_field_not_in_per_item_output(self):
        output = _make_output([
            {"title": "Test", "url": "https://example.com", "content": "Body.", "query": "secret query"},
        ])
        text = _tool.compact_output(output)
        # query appears in header but not as a per-item field
        lines = text.split("\n")
        assert "secret query" in lines[0]  # header
        item_lines = [ln for ln in lines[1:] if ln.strip()]
        for line in item_lines:
            assert "query:" not in line

    def test_title_wraps_with_aligned_continuation(self):
        long_title = "word " * 25  # ~125 chars, exceeds 100-char width
        output = _make_output([
            {"title": long_title.strip(), "url": "https://example.com", "content": "Body.", "query": "q"},
        ])
        text = _tool.compact_output(output)
        lines = text.split("\n")
        title_line = next(ln for ln in lines if "title:" in ln)
        val_start = title_line.index("word")
        title_idx = lines.index(title_line)
        url_idx = next(i for i, ln in enumerate(lines) if "url:" in ln)
        # Should have wrapped to at least 2 lines
        assert url_idx - title_idx > 1, "Long title should wrap"
        for cont in lines[title_idx + 1:url_idx]:
            indent_len = len(cont) - len(cont.lstrip())
            assert indent_len == val_start

    def test_url_force_breaks_with_aligned_continuation(self):
        long_url = "https://example.com/" + "a" * 100  # 120 chars, no spaces
        output = _make_output([
            {"title": "Test", "url": long_url, "content": "Body.", "query": "q"},
        ])
        text = _tool.compact_output(output)
        lines = text.split("\n")
        # No line should exceed width
        for line in lines:
            assert len(line) <= 100, f"Line exceeds width: {line!r}"
        # URL should wrap to multiple lines with aligned continuation
        url_line = next(ln for ln in lines if "url:" in ln)
        val_start = url_line.index("https://")
        url_idx = lines.index(url_line)
        content_idx = next(i for i, ln in enumerate(lines) if "content:" in ln)
        assert content_idx - url_idx > 1, "Long URL should wrap"
        for cont in lines[url_idx + 1:content_idx]:
            indent_len = len(cont) - len(cont.lstrip())
            assert indent_len == val_start

    def test_multi_query_grouped_by_query(self):
        output = _make_output([
            {"title": "Result A1", "url": "https://a1.com", "content": "From query A.", "query": "query A"},
            {"title": "Result B1", "url": "https://b1.com", "content": "From query B.", "query": "query B"},
            {"title": "Result A2", "url": "https://a2.com", "content": "Also query A.", "query": "query A"},
        ])
        text = _tool.compact_output(output)
        # Should have two separate headers
        assert "Web search results for 'query A'" in text
        assert "Web search results for 'query B'" in text
        # Query A should show 2 results, Query B should show 1
        assert "2 results" in text
        assert "1 results" in text
        # Query A block should appear before Query B block
        pos_a = text.index("query A")
        pos_b = text.index("query B")
        assert pos_a < pos_b

    def test_single_query_no_double_spacing(self):
        """Single-query results should not have extra blank lines from grouping."""
        output = _make_output([
            {"title": "Only", "url": "https://only.com", "content": "One query.", "query": "single"},
        ])
        text = _tool.compact_output(output)
        assert "Web search results for 'single'" in text
        assert "1 results" in text

    def test_empty_results(self):
        output = _make_output([])
        text = _tool.compact_output(output)
        assert "0 results" in text
        assert "web search" in text  # fallback query


class TestSearXNGProviderErrors(unittest.IsolatedAsyncioTestCase):
    """Pin that SearXNG error messages name the service so the
    coordinator can plan around it (e.g. apologise about web search
    specifically rather than reporting an opaque error)."""

    async def test_http_error_names_service(self) -> None:
        from app.exceptions import ExternalServiceError

        class _Resp:
            status = 503
            reason = "Service Unavailable"

            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return False
            async def json(self):
                return {}

        class _Session:
            def get(self, *args, **kwargs):
                return _Resp()

        tool = SearXNGSearchTool(SearXNGSearchToolConfig(base_url="http://x"))
        with self.assertRaises(ExternalServiceError) as ctx:
            await tool._fetch_search_results(_Session(), "q", "general")
        self.assertIn("SearXNG", str(ctx.exception))
        self.assertIn("503", str(ctx.exception))

    async def test_connection_error_named_in_wrapped_exception(self) -> None:
        import aiohttp

        from app.exceptions import ExternalServiceError

        class _FailingSession:
            def get(self, *args, **kwargs):
                raise aiohttp.ClientConnectorError(
                    connection_key=None, os_error=OSError("Connection refused"),
                )

        tool = SearXNGSearchTool(SearXNGSearchToolConfig(base_url="http://x"))
        with self.assertRaises(ExternalServiceError) as ctx:
            await tool._fetch_search_results(_FailingSession(), "q", "general")
        msg = str(ctx.exception)
        self.assertIn("SearXNG", msg)
        self.assertTrue(
            "unreachable" in msg.lower() or "connect" in msg.lower(),
            f"expected unreachable/connect framing in {msg!r}",
        )

    async def test_missing_base_url_raises_value_error(self) -> None:
        """SearXNG without a base_url can't search — surface a config
        error rather than silently returning empty results."""
        tool = SearXNGSearchTool(SearXNGSearchToolConfig(base_url=""))
        with self.assertRaises(ValueError) as ctx:
            await tool._fetch_search_results(object(), "q", "general")
        self.assertIn("base_url", str(ctx.exception))


class TestSearXNGPostProcess(unittest.TestCase):
    """The ranking + dedup + title-decoration logic in
    SearXNGSearchTool._post_process. Direct unit tests against a real
    instance — no HTTP, no async."""

    def setUp(self) -> None:
        self.tool = SearXNGSearchTool(SearXNGSearchToolConfig(base_url="http://x"))

    def _result(self, **fields) -> dict:
        return {
            "url": "https://example.com",
            "title": "Title",
            "content": "Body",
            "query": "q",
            **fields,
        }

    def test_sorts_by_score_descending(self):
        out = self.tool._post_process(
            [
                self._result(url="https://a", score=0.1),
                self._result(url="https://b", score=0.9),
                self._result(url="https://c", score=0.5),
            ],
            category=None,
        )
        self.assertEqual([r["url"] for r in out], ["https://b", "https://c", "https://a"])

    def test_dedups_by_url_keeping_highest_score(self):
        """Two results with the same URL — sort by score first, then
        the first occurrence wins, so the higher-scored one is kept."""
        out = self.tool._post_process(
            [
                self._result(url="https://shared", title="Low", score=0.1),
                self._result(url="https://shared", title="High", score=0.9),
            ],
            category=None,
        )
        self.assertEqual(len(out), 1)
        # Higher-score entry wins via sort-then-dedup.
        self.assertEqual(out[0]["title"], "High")

    def test_drops_results_missing_required_fields(self):
        """Results without url / title / content / query are silently
        dropped — partial payloads from a flaky engine shouldn't
        contaminate the output."""
        out = self.tool._post_process(
            [
                {"url": "https://a", "title": "A", "content": "x"},  # no query
                {"url": "https://b", "title": "B", "query": "q"},     # no content
                self._result(url="https://c"),                         # complete
            ],
            category=None,
        )
        self.assertEqual([r["url"] for r in out], ["https://c"])

    def test_published_date_appended_to_title(self):
        out = self.tool._post_process(
            [self._result(publishedDate="2024-03-15")],
            category=None,
        )
        self.assertIn("(Published 2024-03-15)", out[0]["title"])

    def test_category_filter_keeps_matching_only(self):
        """When a category is requested, only results whose own
        ``category`` field matches survive."""
        out = self.tool._post_process(
            [
                self._result(url="https://a", category="news"),
                self._result(url="https://b", category="images"),
                self._result(url="https://c", category="news"),
            ],
            category="news",
        )
        self.assertEqual({r["url"] for r in out}, {"https://a", "https://c"})


if __name__ == "__main__":
    unittest.main()
