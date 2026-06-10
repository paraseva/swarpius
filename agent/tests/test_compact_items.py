import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.roon.compact_formatting import _compact_items  # noqa: E402


def _item(title, reference, group=None, extra_info=None):
    """Helper to build an item dict."""
    d = {"title": title, "reference": reference}
    if group is not None:
        d["group"] = group
    if extra_info is not None:
        d["extra_info"] = extra_info
    return d


def _group(items, group=None):
    """Helper to build a grouped entry (RoonCoreResultsGroupSchema-like)."""
    return {"group": group, "items": items}


class TestActionItemFiltering(unittest.TestCase):
    """Verify that _compact_items strips action items from results so the
    coordinator never sees them.  Action items (Play Now, Add Next, Queue,
    Start Radio, Play Album, etc.) are handled by roon_action — exposing
    them via browse risks accidental playback."""

    # ── Pure action menus → empty ────────────────────────────────────

    def test_track_action_menu_returns_empty(self):
        """Drilling into a track yields Play Now / Add Next / Queue / Start Radio.
        All are action items — result should be empty."""
        items = [
            _item("Play Now", "a1"),
            _item("Add Next", "a2"),
            _item("Queue", "a3"),
            _item("Start Radio", "a4"),
        ]
        self.assertEqual(_compact_items(items), [])

    # ── Mixed results: action + content → content only ───────────────

    def test_album_listing_strips_play_album(self):
        """Album drill-down returns Play Album at index 0 + tracks.
        Only tracks should appear, indexed from 1."""
        items = [
            _item("Play Album", "action:0"),
            _item("Track 1", "t1:1"),
            _item("Track 2", "t2:2"),
        ]
        result = _compact_items(items)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].startswith("(1)"))
        self.assertTrue(result[1].startswith("(2)"))
        self.assertIn("Track 1", result[0])
        self.assertIn("Track 2", result[1])

    # ── Grouped format ───────────────────────────────────────────────

    def test_grouped_strips_action_items(self):
        payload = [
            _group([
                _item("Play Album", "action:0"),
                _item("Track 1", "t1:1"),
                _item("Track 2", "t2:2"),
            ]),
        ]
        result = _compact_items(payload)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].startswith("(1)"))
        self.assertTrue(result[1].startswith("(2)"))

    def test_grouped_all_actions_returns_empty(self):
        payload = [
            _group([
                _item("Play Now", "a1"),
                _item("Add Next", "a2"),
                _item("Queue", "a3"),
                _item("Start Radio", "a4"),
            ]),
        ]
        self.assertEqual(_compact_items(payload), [])

    # ── Content-only results unchanged ───────────────────────────────

    def test_regular_results_indexed_from_1(self):
        items = [
            _item("Abbey Road", "alb1:0", extra_info="The Beatles"),
            _item("Abbey Road (Remaster)", "alb2:1", extra_info="The Beatles"),
        ]
        result = _compact_items(items)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].startswith("(1)"))
        self.assertTrue(result[1].startswith("(2)"))

    def test_search_root_categories_unchanged(self):
        """Search root results (Artists, Albums, Tracks categories) have no
        action items and should pass through unchanged."""
        items = [
            _item("Pat Benatar", "5661c", extra_info="9 Albums"),
            _item("Artists", "381ac", extra_info="1 Result"),
            _item("Albums", "96ba0", extra_info="26 Results"),
            _item("Tracks", "750e6", extra_info="69 Results"),
        ]
        result = _compact_items(items)
        self.assertEqual(len(result), 4)
        self.assertTrue(result[0].startswith("(1)"))
        self.assertTrue(result[3].startswith("(4)"))

    def test_grouped_multi_group_content_only(self):
        payload = [
            _group([
                _item("Abbey Road", "alb1:0", extra_info="The Beatles"),
            ], group="Albums"),
            _group([
                _item("Abbey Road (Live)", "alb2:1", extra_info="The Beatles"),
            ], group="Live Albums"),
        ]
        result = _compact_items(payload)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].startswith("(1)"))
        self.assertTrue(result[1].startswith("(2)"))

    # ── Edge cases ───────────────────────────────────────────────────

    def test_empty_payload(self):
        self.assertEqual(_compact_items([]), [])

    def test_single_regular_item_starts_at_1(self):
        items = [_item("Some Album", "a1:0")]
        result = _compact_items(items)
        self.assertTrue(result[0].startswith("(1)"))


# ── Track number stripping ──────────────────────────────────────────

class TestTrackNumberStripping(unittest.TestCase):
    """Track titles with leading "N. " prefixes should have the prefix
    stripped so the compacted (N) index is the sole numbering."""

    def test_single_disc_strips_leading_numbers(self):
        """Titles like '1. Track Name' become '(1) [ref] Track Name'."""
        items = [
            _item("1. I Think We're Alone Now (Re-Recorded)", "t1", extra_info="Tiffany"),
            _item("2. Could've Been (Re-Recorded)", "t2", extra_info="Tiffany"),
            _item("3. Venus", "t3", extra_info="Tiffany"),
        ]
        result = _compact_items(items)
        self.assertEqual(len(result), 3)
        self.assertIn("(1) [t1] I Think We're Alone Now (Re-Recorded)", result[0])
        self.assertIn("(2) [t2] Could've Been (Re-Recorded)", result[1])
        self.assertIn("(3) [t3] Venus", result[2])

    def test_does_not_strip_when_number_is_part_of_title(self):
        """Titles like '1999' or '10cc' should NOT be stripped."""
        items = [
            _item("1999", "t1", extra_info="Prince"),
            _item("10cc", "t2"),
        ]
        result = _compact_items(items)
        self.assertIn("1999", result[0])
        self.assertIn("10cc", result[1])

    def test_playlist_with_one_digit_prefixed_title_is_not_stripped(self):
        """A playlist that includes a single legitimately digit-prefixed
        title (e.g. '2 Minutes to Midnight') must not have that "2 "
        stripped — stripping fires only when the whole list is
        consistently numbered like an album."""
        items = [
            _item("Hallowed Be Thy Name", "t1", extra_info="Iron Maiden"),
            _item("2 Minutes to Midnight (2015 Remaster)", "t2", extra_info="Iron Maiden"),
            _item("Eddie", "t3", extra_info="Iron Maiden"),
        ]
        result = _compact_items(items)
        self.assertIn("2 Minutes to Midnight", result[1])

    def test_strips_when_sequence_starts_at_two(self):
        """Roon occasionally returns track lists missing the first item
        (e.g. a partial drill-in). A sequence starting at 2 should still
        be recognised as an album-style list, have prefixes stripped,
        and use the extracted track number as the (idx) value."""
        items = [
            _item("2. Track B", "t2"),
            _item("3. Track C", "t3"),
            _item("4. Track D", "t4"),
        ]
        result = _compact_items(items)
        self.assertIn("(2) [t2] Track B", result[0])
        self.assertIn("(3) [t3] Track C", result[1])
        self.assertIn("(4) [t4] Track D", result[2])

    def test_strips_when_sequence_has_gaps(self):
        """Roon sometimes returns partial track lists with gaps (e.g.
        tracks 1, 2, 3, 5, 6). The list is still recognisably album-
        numbered (strictly increasing N. prefixes), so prefixes strip,
        and the (idx) values reflect the *extracted* track number — so
        the gap from 3 to 5 stays visible to the coordinator."""
        items = [
            _item("1. Track A", "t1"),
            _item("2. Track B", "t2"),
            _item("3. Track C", "t3"),
            _item("5. Track D", "t5"),
            _item("6. Track E", "t6"),
        ]
        result = _compact_items(items)
        self.assertIn("(1) [t1] Track A", result[0])
        self.assertIn("(2) [t2] Track B", result[1])
        self.assertIn("(3) [t3] Track C", result[2])
        self.assertIn("(5) [t5] Track D", result[3])
        self.assertIn("(6) [t6] Track E", result[4])

    def test_strips_when_sequence_starts_high_with_gaps(self):
        """A partial run starting above 2 is still treated as a numbered
        list (provided every title has the N. form and numbers are
        strictly increasing). Extracted track numbers used as (idx)."""
        items = [
            _item("3. Track C", "t3"),
            _item("5. Track E", "t5"),
            _item("8. Track H", "t8"),
        ]
        result = _compact_items(items)
        self.assertIn("(3) [t3] Track C", result[0])
        self.assertIn("(5) [t5] Track E", result[1])
        self.assertIn("(8) [t8] Track H", result[2])

    def test_does_not_strip_when_numbers_not_increasing(self):
        """Even when every title is digit-prefixed, the numbers must form
        a strictly increasing sequence — otherwise it's a playlist
        coincidence, not an album."""
        items = [
            _item("1. Foo", "t1"),
            _item("4. Bar", "t2"),
            _item("2. Baz", "t3"),
        ]
        result = _compact_items(items)
        self.assertIn("1. Foo", result[0])
        self.assertIn("4. Bar", result[1])
        self.assertIn("2. Baz", result[2])

    def test_does_not_strip_when_one_item_lacks_prefix(self):
        """A single missing prefix flips off stripping — albums are
        consistently numbered, so any unnumbered track means it's not an
        album view."""
        items = [
            _item("1. First", "t1"),
            _item("Second", "t2"),  # no prefix
            _item("3. Third", "t3"),
        ]
        result = _compact_items(items)
        self.assertIn("1. First", result[0])
        self.assertIn("Second", result[1])
        self.assertIn("3. Third", result[2])

    def test_does_not_strip_when_prefix_lacks_dot(self):
        """Track-number stripping requires the dot in 'N. Title' format.
        Roon's actual track numbering always uses the dot; the dot-less
        form (e.g. '2 Minutes to Midnight') is a real title and must
        not be mangled."""
        items = [
            _item("2 Minutes to Midnight", "t1", extra_info="Iron Maiden"),
            _item("3 AM", "t2", extra_info="Matchbox Twenty"),
            _item("5 Years Time", "t3", extra_info="Noah and the Whale"),
        ]
        result = _compact_items(items)
        self.assertIn("2 Minutes to Midnight", result[0])
        self.assertIn("3 AM", result[1])
        self.assertIn("5 Years Time", result[2])

    def test_non_numbered_titles_unchanged(self):
        items = [
            _item("Abbey Road", "a1", extra_info="The Beatles"),
            _item("Dark Side of the Moon", "a2", extra_info="Pink Floyd"),
        ]
        result = _compact_items(items)
        self.assertIn("Abbey Road", result[0])
        self.assertIn("Dark Side of the Moon", result[1])

    def test_grouped_format_also_stripped(self):
        payload = [
            _group([
                _item("Play Album", "action:0"),
                _item("1. Track A", "t1"),
                _item("2. Track B", "t2"),
            ]),
        ]
        result = _compact_items(payload)
        self.assertEqual(len(result), 2)
        self.assertIn("(1) [t1] Track A", result[0])
        self.assertIn("(2) [t2] Track B", result[1])


# ── Multi-disc grouping ────────────────────────────────────────────

class TestMultiDiscGrouping(unittest.TestCase):
    """Multi-disc albums with 'd-t Title' format should be grouped by disc
    with [Disc N] headers and per-disc numbering."""

    def test_two_discs_grouped_with_headers(self):
        items = [
            _item("1-1 I Think We're Alone Now", "t01", extra_info="Tiffany"),
            _item("1-2 The Only Way Is Up", "t02", extra_info="Yazz"),
            _item("2-1 Call Me", "t03", extra_info="Ivana Spagna"),
            _item("2-2 Tell It To My Heart", "t04", extra_info="Taylor Dayne"),
        ]
        result = _compact_items(items)
        self.assertEqual(result[0], "[Disc 1] (2 tracks)")
        self.assertIn("(1) [t01] I Think We're Alone Now", result[1])
        self.assertIn("(2) [t02] The Only Way Is Up", result[2])
        self.assertEqual(result[3], "[Disc 2] (2 tracks)")
        self.assertIn("(1) [t03] Call Me", result[4])
        self.assertIn("(2) [t04] Tell It To My Heart", result[5])

    def test_three_discs(self):
        items = [
            _item("1-1 Track A", "a1"),
            _item("1-2 Track B", "a2"),
            _item("2-1 Track C", "a3"),
            _item("3-1 Track D", "a4"),
            _item("3-2 Track E", "a5"),
            _item("3-3 Track F", "a6"),
        ]
        result = _compact_items(items)
        # Disc 1: header + 2 tracks
        self.assertEqual(result[0], "[Disc 1] (2 tracks)")
        # Disc 2: header + 1 track
        self.assertEqual(result[3], "[Disc 2] (1 track)")
        # Disc 3: header + 3 tracks
        self.assertEqual(result[5], "[Disc 3] (3 tracks)")
        self.assertEqual(len(result), 9)  # 3 headers + 6 tracks

    def test_multi_disc_strips_prefix_from_title(self):
        items = [
            _item("1-1 I Think We're Alone Now", "t01"),
        ]
        result = _compact_items(items)
        self.assertNotIn("1-1", result[1])
        self.assertIn("I Think We're Alone Now", result[1])

    def test_multi_disc_restarts_numbering_per_disc(self):
        items = [
            _item("1-1 A", "a1"),
            _item("1-2 B", "a2"),
            _item("2-1 C", "a3"),
        ]
        result = _compact_items(items)
        # Disc 2 track should be (1), not (3)
        self.assertTrue(result[4].startswith("(1)"))

    def test_action_items_stripped_before_disc_grouping(self):
        payload = [
            _group([
                _item("Play Album", "action:0"),
                _item("1-1 Track A", "t1"),
                _item("1-2 Track B", "t2"),
                _item("2-1 Track C", "t3"),
            ]),
        ]
        result = _compact_items(payload)
        self.assertEqual(result[0], "[Disc 1] (2 tracks)")
        self.assertEqual(result[3], "[Disc 2] (1 track)")

    def test_mixed_disc_and_non_disc_not_grouped(self):
        """If only some items have the d-t pattern, don't group — treat as normal."""
        items = [
            _item("1-1 Track A", "t1"),
            _item("Bonus Track", "t2"),
        ]
        result = _compact_items(items)
        # Should NOT have disc headers — mixed format
        self.assertFalse(any(line.startswith("[Disc") for line in result))

    def test_single_disc_prefix_not_treated_as_multi_disc(self):
        """Titles like '1. Track' should NOT trigger multi-disc grouping."""
        items = [
            _item("1. Track A", "t1"),
            _item("2. Track B", "t2"),
        ]
        result = _compact_items(items)
        self.assertFalse(any(line.startswith("[Disc") for line in result))

    def test_multi_disc_uses_extracted_track_numbers_when_gappy(self):
        """When Roon returns a partial multi-disc result like
        '1-1, 1-3, 1-4, 1-15, 2-6, 2-7, 3-3, 3-4', the rendered
        per-disc (idx) values must be the *extracted* track numbers
        so gaps stay visible to the coordinator."""
        items = [
            _item("1-1 Track A", "a1"),
            _item("1-3 Track B", "a3"),
            _item("1-4 Track C", "a4"),
            _item("1-15 Track D", "a15"),
            _item("2-6 Track E", "b6"),
            _item("2-7 Track F", "b7"),
            _item("3-3 Track G", "c3"),
            _item("3-4 Track H", "c4"),
        ]
        result = _compact_items(items)
        # Disc 1: 4 tracks numbered 1, 3, 4, 15
        self.assertEqual(result[0], "[Disc 1] (4 tracks)")
        self.assertIn("(1) [a1] Track A", result[1])
        self.assertIn("(3) [a3] Track B", result[2])
        self.assertIn("(4) [a4] Track C", result[3])
        self.assertIn("(15) [a15] Track D", result[4])
        # Disc 2: 2 tracks numbered 6, 7
        self.assertEqual(result[5], "[Disc 2] (2 tracks)")
        self.assertIn("(6) [b6] Track E", result[6])
        self.assertIn("(7) [b7] Track F", result[7])
        # Disc 3: 2 tracks numbered 3, 4
        self.assertEqual(result[8], "[Disc 3] (2 tracks)")
        self.assertIn("(3) [c3] Track G", result[9])
        self.assertIn("(4) [c4] Track H", result[10])


if __name__ == "__main__":
    unittest.main()
