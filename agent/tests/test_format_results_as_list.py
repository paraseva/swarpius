"""Tests for the result-tag formatter in ``app/roon/tag_expansion.py``.

``_format_results_as_list`` renders cached search results as one or
more ``<list>`` blocks for the chat panel. Coverage was a recon gap —
the function was only exercised indirectly through ``expand_list_tags``
in queue tests. This file targets it directly.
"""

import unittest

from app.roon.tag_expansion import (
    _flatten_groups,
    _format_results_as_list,
    _get_first_item_title_for_tag,
    _parse_list_tag_attrs,
)


def _item(title: str, **extras) -> dict:
    """Minimal item shape: title plus optional group/extra_info."""
    return {"title": title, **extras}


def _grouped(items: list) -> list:
    """Wrap items in the {items: [...]} group shape Roon returns."""
    return [{"items": items}]


class TestFormatResultsAsList(unittest.TestCase):

    def test_empty_groups_returns_empty_string(self):
        self.assertEqual(_format_results_as_list([]), "")
        self.assertEqual(_format_results_as_list(_grouped([])), "")

    def test_single_disc_numbered_strips_prefixes(self):
        """Tracks like "1. Foo" / "2. Bar" → prefixes stripped from
        rendered titles AND replaced by ``(N)`` numbering."""
        items = [
            _item("1. Time Out"),
            _item("2. In a Sentimental Mood"),
            _item("3. Round Midnight"),
        ]
        out = _format_results_as_list(items)
        self.assertIn("<list>", out)
        self.assertIn("<summary>Search results (3 items)</summary>", out)
        self.assertIn("1. Time Out", out)
        self.assertNotIn("1. 1.", out)
        self.assertIn("2. In a Sentimental Mood", out)

    def test_single_disc_non_numbered_keeps_titles(self):
        """Titles without a number prefix are rendered as-is, just
        with the (1) / (2) / ... index."""
        items = [_item("Kind of Blue"), _item("Take Five")]
        out = _format_results_as_list(items)
        self.assertIn("1. Kind of Blue", out)
        self.assertIn("2. Take Five", out)

    def test_multi_disc_nests_one_outer_and_per_disc_inner_blocks(self):
        """Items shaped "d-t Title" trigger multi-disc rendering — one
        outer summary "(N tracks, M discs)" wrapping per-disc inners."""
        items = [
            _item("1-1 Disc One Track A"),
            _item("1-2 Disc One Track B"),
            _item("2-1 Disc Two Track A"),
            _item("2-2 Disc Two Track B"),
            _item("2-3 Disc Two Track C"),
        ]
        out = _format_results_as_list(items, title="The Album")
        self.assertIn("<summary>The Album (5 tracks, 2 discs)</summary>", out)
        self.assertIn("<summary>Disc 1 (2 tracks)</summary>", out)
        self.assertIn("<summary>Disc 2 (3 tracks)</summary>", out)
        # Per-disc bodies number from 1.
        self.assertIn("1. Disc One Track A", out)
        self.assertIn("2. Disc One Track B", out)
        self.assertIn("1. Disc Two Track A", out)

    def test_single_disc_numbered_with_gaps_uses_extracted_numbers(self):
        """Partial single-disc albums (e.g. tracks 1, 2, 3, 5, 6) render
        with the *extracted* track number, so the gap stays visible to
        the user in the chat panel."""
        items = [
            _item("1. Track A"),
            _item("2. Track B"),
            _item("3. Track C"),
            _item("5. Track D"),
            _item("6. Track E"),
        ]
        out = _format_results_as_list(items)
        self.assertIn("1. Track A", out)
        self.assertIn("2. Track B", out)
        self.assertIn("3. Track C", out)
        self.assertIn("5. Track D", out)
        self.assertIn("6. Track E", out)
        # No row numbered 4 (that's the gap)
        self.assertNotIn("4. ", out)

    def test_multi_disc_with_gaps_uses_extracted_track_numbers(self):
        """Partial multi-disc albums render with per-disc *extracted*
        track numbers, preserving gap signal in the chat panel."""
        items = [
            _item("1-1 Track A"),
            _item("1-3 Track B"),
            _item("1-4 Track C"),
            _item("1-15 Track D"),
            _item("2-6 Track E"),
            _item("2-7 Track F"),
        ]
        out = _format_results_as_list(items, title="The Album")
        self.assertIn("<summary>The Album (6 tracks, 2 discs)</summary>", out)
        self.assertIn("<summary>Disc 1 (4 tracks)</summary>", out)
        self.assertIn("<summary>Disc 2 (2 tracks)</summary>", out)
        # Disc 1 tracks numbered 1, 3, 4, 15
        self.assertIn("1. Track A", out)
        self.assertIn("3. Track B", out)
        self.assertIn("4. Track C", out)
        self.assertIn("15. Track D", out)
        # Disc 2 tracks numbered 6, 7
        self.assertIn("6. Track E", out)
        self.assertIn("7. Track F", out)
        # No row numbered 2 in Disc 1 (gap)
        self.assertNotIn("2. Track", out)

    def test_single_track_disc_uses_singular_word(self):
        """Disc with one track uses "1 track", not "1 tracks"."""
        items = [_item("1-1 Lone Track"), _item("2-1 Other Lone Track")]
        out = _format_results_as_list(items)
        self.assertIn("<summary>Disc 1 (1 track)</summary>", out)
        self.assertIn("<summary>Disc 2 (1 track)</summary>", out)

    def test_action_first_item_is_dropped(self):
        """When the first item is an action like "Play Album", it's
        a Roon gateway artefact, not content — drop it before formatting."""
        items = _grouped([
            _item("Play Album"),
            _item("Track 1"),
            _item("Track 2"),
        ])
        out = _format_results_as_list(items)
        self.assertNotIn("Play Album", out)
        self.assertIn("1. Track 1", out)
        self.assertIn("2. Track 2", out)
        self.assertIn("(2 items)", out)

    def test_only_action_items_returns_empty(self):
        """A list consisting only of actions has no content to render."""
        items = _grouped([
            _item("Play Album"),
        ])
        self.assertEqual(_format_results_as_list(items), "")

    def test_singular_item_count_uses_singular_label(self):
        """One item → ``(1 item)``, not ``(1 items)``."""
        out = _format_results_as_list([_item("Lone Item")])
        self.assertIn("(1 item)</summary>", out)

    def test_custom_title_used_in_summary(self):
        """Caller-supplied title replaces the default "Search results"."""
        out = _format_results_as_list(
            [_item("Track A"), _item("Track B")], title="Album X",
        )
        self.assertIn("<summary>Album X (2 items)</summary>", out)

    def test_group_and_extra_info_appended_with_em_dash(self):
        """Items with `group` and `extra_info` render as
        ``N. Title — Group — Extra``."""
        out = _format_results_as_list([
            _item("Take Five", group="Jazz", extra_info="1959"),
        ])
        self.assertIn("1. Take Five — Jazz — 1959", out)
        # Sentinel group "-" is suppressed.
        out2 = _format_results_as_list([_item("X", group="-", extra_info="E")])
        self.assertIn("1. X — E", out2)
        self.assertNotIn(" — - — ", out2)


class TestTagExpansionHelpers(unittest.TestCase):

    def test_parse_list_tag_attrs_extracts_ref_and_title(self):
        attrs = _parse_list_tag_attrs('ref="res_00001" title="My Album"')
        self.assertEqual(attrs["ref"], "res_00001")
        self.assertEqual(attrs["title"], "My Album")

    def test_parse_list_tag_attrs_ignores_unknown_keys(self):
        attrs = _parse_list_tag_attrs('ref="r1" foo="bar"')
        self.assertEqual(attrs["ref"], "r1")
        self.assertIsNone(attrs["title"])
        self.assertNotIn("foo", attrs)

    def test_get_first_item_title_handles_flat_and_grouped(self):
        self.assertEqual(
            _get_first_item_title_for_tag([_item("Direct")]),
            "Direct",
        )
        self.assertEqual(
            _get_first_item_title_for_tag(_grouped([_item("In Group")])),
            "In Group",
        )
        self.assertIsNone(_get_first_item_title_for_tag([]))

    def test_flatten_groups_pulls_items_out_of_group_envelope(self):
        flat = _flatten_groups(_grouped([_item("A"), _item("B")]))
        self.assertEqual([i["title"] for i in flat], ["A", "B"])

    def test_flatten_groups_passes_through_bare_items(self):
        flat = _flatten_groups([_item("X"), _item("Y")])
        self.assertEqual([i["title"] for i in flat], ["X", "Y"])


if __name__ == "__main__":
    unittest.main()
