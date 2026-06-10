"""Tests for ``render_block_tags_for_cli`` — the generic
HTML-block-to-Rich-markup renderer used by the CLI chat panel.

Any ``<tag>...</tag>`` block is stripped of its wrapping tags. If
the block has a ``<summary>...</summary>`` child, the summary text
renders as a bold Rich header above the body. Nesting (same-tag
and cross-tag) is handled by iterating innermost-first."""

import unittest

from app.coordinator.sanitise import render_block_tags_for_cli


class TestRenderExtendedInfo(unittest.TestCase):
    """Pre-existing ``<extended_info>`` contract — must keep working
    under the generic renderer."""

    def test_no_block_returns_unchanged(self):
        text = "Just some plain text."
        self.assertEqual(render_block_tags_for_cli(text), text)

    def test_extended_info_content_kept(self):
        text = "Hello.\n<extended_info><summary>Info</summary>\n1. Track A\n2. Track B\n</extended_info>\nDone."
        result = render_block_tags_for_cli(text)
        self.assertNotIn("<extended_info>", result)
        self.assertNotIn("</extended_info>", result)
        self.assertNotIn("<summary>", result)
        self.assertIn("Track A", result)
        self.assertIn("Track B", result)

    def test_newline_separation(self):
        text = "Before.\n<extended_info><summary>Info</summary>\nList item\n</extended_info>\nAfter."
        result = render_block_tags_for_cli(text)
        lines = result.split("\n")
        before_idx = next(i for i, line in enumerate(lines) if "Before." in line)
        content_idx = next(i for i, line in enumerate(lines) if "List item" in line)
        between = lines[before_idx + 1:content_idx]
        self.assertTrue(any(line.strip() == "" for line in between))

    def test_summary_rendered_as_bold_header(self):
        text = "<extended_info><summary>Search results</summary>\n1. Item\n</extended_info>"
        result = render_block_tags_for_cli(text)
        self.assertIn("Search results", result)
        self.assertIn("[bold]", result)
        self.assertIn("[/bold]", result)
        self.assertIn("Item", result)
        self.assertLess(result.index("Search results"), result.index("Item"))

    def test_body_only_no_summary_renders_without_header(self):
        text = "<extended_info>\nJust the body\n</extended_info>"
        result = render_block_tags_for_cli(text)
        self.assertIn("Just the body", result)
        self.assertNotIn("[bold]", result)

    def test_multiple_extended_info_blocks(self):
        text = "A.\n<extended_info><summary>X</summary>\nFirst\n</extended_info>\nB.\n<extended_info><summary>Y</summary>\nSecond\n</extended_info>\nC."
        result = render_block_tags_for_cli(text)
        for label in ("First", "Second", "A.", "B.", "C."):
            self.assertIn(label, result)

    def test_empty_extended_info(self):
        text = "Before.\n<extended_info><summary>Empty</summary>\n</extended_info>\nAfter."
        result = render_block_tags_for_cli(text)
        self.assertIn("Before.", result)
        self.assertIn("After.", result)

    def test_none_returns_none(self):
        self.assertIsNone(render_block_tags_for_cli(None))


class TestRenderListBlocks(unittest.TestCase):
    """``<list>`` blocks produced by ``app.roon.tag_expansion`` must
    render the same way ``<extended_info>`` does (summary → bold
    header, body → content)."""

    def test_simple_list_renders_summary_as_bold_header(self):
        text = "<list><summary>Search results (3 items)</summary>\n\n1. Track A\n2. Track B\n3. Track C\n</list>"
        result = render_block_tags_for_cli(text)
        self.assertNotIn("<list>", result)
        self.assertNotIn("</list>", result)
        self.assertNotIn("<summary>", result)
        self.assertIn("Search results (3 items)", result)
        self.assertIn("[bold]", result)
        self.assertIn("[/bold]", result)
        self.assertIn("Track A", result)
        self.assertIn("Track C", result)
        self.assertLess(result.index("Search results"), result.index("Track A"))

    def test_nested_multi_disc_list_renders_inner_first(self):
        # Real shape produced by tag_expansion._format_results_as_list
        # for a multi-disc album.
        text = (
            "<list><summary>Album X (4 tracks, 2 discs)</summary>\n\n"
            "<list><summary>Disc 1 (2 tracks)</summary>\n\n1. A\n2. B\n</list>\n\n"
            "<list><summary>Disc 2 (2 tracks)</summary>\n\n1. C\n2. D\n</list>\n\n"
            "</list>"
        )
        result = render_block_tags_for_cli(text)
        self.assertNotIn("<list>", result)
        self.assertNotIn("</list>", result)
        self.assertIn("Album X (4 tracks, 2 discs)", result)
        self.assertIn("Disc 1", result)
        self.assertIn("Disc 2", result)
        for label in ("A", "B", "C", "D"):
            self.assertIn(label, result)
        self.assertLess(result.index("Album X"), result.index("Disc 1"))
        self.assertLess(result.index("Disc 1"), result.index("Disc 2"))

    def test_multiple_top_level_lists(self):
        text = (
            "Before.\n"
            "<list><summary>First</summary>\n\n1. one\n</list>\n"
            "Middle.\n"
            "<list><summary>Second</summary>\n\n1. two\n</list>\n"
            "After."
        )
        result = render_block_tags_for_cli(text)
        for label in ("Before.", "Middle.", "After.", "First", "Second", "one", "two"):
            self.assertIn(label, result)
        self.assertNotIn("<list>", result)


class TestRenderArbitraryTag(unittest.TestCase):
    """Future paired tags must render without code changes — the whole
    point of the generic renderer. ``<summary>`` still maps to bold;
    unknown wrapper is stripped, body preserved."""

    def test_unknown_tag_with_summary(self):
        text = "<future_tag><summary>Heading</summary>\nbody line\n</future_tag>"
        result = render_block_tags_for_cli(text)
        self.assertNotIn("<future_tag>", result)
        self.assertIn("Heading", result)
        self.assertIn("[bold]", result)
        self.assertIn("body line", result)

    def test_unknown_tag_without_summary(self):
        text = "<future_tag>just body</future_tag>"
        result = render_block_tags_for_cli(text)
        self.assertNotIn("<future_tag>", result)
        self.assertIn("just body", result)
        self.assertNotIn("[bold]", result)

    def test_orphan_summary_loses_tags_but_keeps_text(self):
        # Free-floating <summary> outside any block — the wrapper is
        # stripped at the end and the text survives. No bold (no
        # parent block to attach it to).
        text = "Lead-in.\n<summary>Orphaned</summary>\nTrailing."
        result = render_block_tags_for_cli(text)
        self.assertNotIn("<summary>", result)
        self.assertIn("Orphaned", result)


if __name__ == "__main__":
    unittest.main()
