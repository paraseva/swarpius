"""Behaviour contract for the pure-function fuzzy matching helpers
in ``roon_core.fuzzy_match``.
"""

from __future__ import annotations

import unittest

from roon_core.browse_session import ItemIdentity
from roon_core.fuzzy_match import fuzzy_find, fuzzy_match_and_sort, normalise_title
from roon_core.schemas import RoonCoreItemSchema


def _item(
    title: str,
    subtitle: str = "",
    hint: str | None = None,
    image_key: str | None = None,
) -> RoonCoreItemSchema:
    return RoonCoreItemSchema(
        title=title, subtitle=subtitle, hint=hint, image_key=image_key,
    )


class TestNormaliseTitle(unittest.TestCase):
    def test_strips_leading_track_number(self):
        self.assertEqual(
            normalise_title("1. Rivers of Babylon"),
            "rivers of babylon",
        )

    def test_lowercases_the_remainder(self):
        self.assertEqual(
            normalise_title("Rivers Of Babylon"),
            "rivers of babylon",
        )

    def test_strips_trailing_punctuation(self):
        self.assertEqual(
            normalise_title("Track Name!!!"),
            "track name",
        )

    def test_preserves_internal_non_alpha(self):
        self.assertEqual(
            normalise_title("(Remix) Track Name"),
            "remix) track name",
        )

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalise_title(""), "")


class TestFuzzyMatchAndSort(unittest.TestCase):
    def test_empty_items_returns_empty_list(self):
        self.assertEqual(fuzzy_match_and_sort([], ["query"]), [])

    def test_items_below_threshold_are_dropped(self):
        items = [
            _item("xyz totally unrelated abc"),
            _item("rivers of babylon"),
        ]
        result = fuzzy_match_and_sort(
            items, ["rivers of babylon"], threshold=50,
        )
        titles = [r.title for r in result]
        self.assertIn("rivers of babylon", titles)
        self.assertNotIn("xyz totally unrelated abc", titles)

    def test_results_are_sorted_descending_by_match_quality(self):
        items = [
            _item("rivers babylon partial"),
            _item("rivers of babylon"),
        ]
        result = fuzzy_match_and_sort(
            items, ["rivers of babylon"],
        )
        self.assertEqual(result[0].title, "rivers of babylon")

    def test_multiple_sort_strings_are_joined_into_one_query(self):
        items = [_item("hello world greeting")]
        joined = fuzzy_match_and_sort(items, ["hello", "world"])
        single = fuzzy_match_and_sort(items, ["hello world"])
        self.assertEqual(
            [r.title for r in joined], [r.title for r in single],
        )

    def test_alternate_field_matches_when_field_to_match_is_set(self):
        items = [_item("ignored title", subtitle="john lennon")]
        result = fuzzy_match_and_sort(
            items, ["lennon"], field_to_match="subtitle",
        )
        self.assertEqual(len(result), 1)


class TestFuzzyFind(unittest.TestCase):
    def test_empty_items_returns_none(self):
        identity = ItemIdentity(title="anything")
        self.assertIsNone(fuzzy_find([], identity))

    def test_returns_best_matching_item(self):
        items = [
            _item("rivers babylon partial", subtitle="Boney M"),
            _item("rivers of babylon", subtitle="Boney M"),
        ]
        identity = ItemIdentity(title="rivers of babylon", subtitle="Boney M")
        result = fuzzy_find(items, identity)
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "rivers of babylon")

    def test_no_match_above_threshold_returns_none(self):
        items = [_item("xyz totally unrelated abc", subtitle="nobody")]
        identity = ItemIdentity(
            title="rivers of babylon", subtitle="Boney M",
        )
        self.assertIsNone(fuzzy_find(items, identity, threshold=75))

    def test_hint_mismatch_excludes_item(self):
        """When both identity and item have a hint, they must match
        — even a perfect title+subtitle match is rejected if hints differ."""
        items = [_item("Thriller", subtitle="Michael Jackson", hint="Album")]
        identity = ItemIdentity(
            title="Thriller", subtitle="Michael Jackson", hint="Track",
        )
        self.assertIsNone(fuzzy_find(items, identity))

    def test_hint_match_keeps_item_in_consideration(self):
        items = [_item("Thriller", subtitle="Michael Jackson", hint="Album")]
        identity = ItemIdentity(
            title="Thriller", subtitle="Michael Jackson", hint="Album",
        )
        result = fuzzy_find(items, identity)
        self.assertIsNotNone(result)
        self.assertEqual(result.hint, "Album")

    def test_subtitle_picks_correct_among_same_titles(self):
        """When titles are identical and the identity carries a
        subtitle, the item whose subtitle matches wins."""
        items = [
            _item("Yesterday", subtitle="Other Artist"),
            _item("Yesterday", subtitle="The Beatles"),
        ]
        identity = ItemIdentity(title="Yesterday", subtitle="The Beatles")
        result = fuzzy_find(items, identity)
        self.assertIsNotNone(result)
        self.assertEqual(result.subtitle, "The Beatles")

    def test_default_threshold_requires_subtitle(self):
        """The default threshold (75) plus the 70/30 title/subtitle
        weighting means an identity without subtitle can never score
        above 70 — even a perfect title match returns None. Production
        callers always supply identities built from real Roon items
        (which carry artist subtitles), so this isn't hit in practice;
        the test pins it so a future refactor of the scoring formula
        doesn't silently change the contract."""
        items = [_item("rivers of babylon")]
        identity = ItemIdentity(title="rivers of babylon")
        self.assertIsNone(fuzzy_find(items, identity))

    def test_image_key_disambiguates_same_title_and_subtitle(self):
        """Two items identical in title and subtitle (e.g. two releases of one
        album) are separated by image_key — the identity's image_key selects its
        own item, not whichever happens to come first."""
        items = [
            _item("Greatest Hits", subtitle="The Band", image_key="img-1"),
            _item("Greatest Hits", subtitle="The Band", image_key="img-2"),
        ]
        identity = ItemIdentity(
            title="Greatest Hits", subtitle="The Band", image_key="img-2",
        )
        result = fuzzy_find(items, identity)
        self.assertIsNotNone(result)
        self.assertEqual(result.image_key, "img-2")

    def test_image_key_ignored_when_absent_on_either_side(self):
        """image_key only filters when present on both sides — a missing
        image_key never excludes an otherwise-matching item."""
        items = [_item("Yesterday", subtitle="The Beatles")]  # no image_key
        identity = ItemIdentity(
            title="Yesterday", subtitle="The Beatles", image_key="img-x",
        )
        self.assertIsNotNone(fuzzy_find(items, identity))


if __name__ == "__main__":
    unittest.main()
