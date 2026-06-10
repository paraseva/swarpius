"""Unit tests for the title-based reference recovery helper.

When the coordinator submits a mistyped reference to `roon_action`, we want
to fall back to the submitted `title` and look it up in the result_store.
Outcomes:

- 0 title matches → ``NoTitleMatch``
- 1 title match → ``UniqueTitleMatch``
- 2+ title matches, clear fuzzy winner (strictly closer than runner-up) →
  ``FuzzyTitleWinner``
- 2+ title matches, tied fuzzy distance → ``AmbiguousTitleTie``
"""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.runtime.result_store_recovery import (  # noqa: E402
    AmbiguousTitleTie,
    FuzzyTitleWinner,
    NoTitleMatch,
    RecoveryOutcome,
    ReferenceCandidateMatch,
    UniqueTitleMatch,
    levenshtein,
    lookup_references_for_title,
    lookup_title_for_reference,
    recover_reference,
    titles_match,
)


def _group(items):
    """Build a roon_search-style group payload."""
    return [{"group": "-", "items": items}]


class TestLevenshtein(unittest.TestCase):
    def test_zero_on_identical(self):
        self.assertEqual(levenshtein("3d8cc", "3d8cc"), 0)

    def test_single_substitution(self):
        self.assertEqual(levenshtein("3d8cc", "3d7cc"), 1)

    def test_insertion(self):
        self.assertEqual(levenshtein("02def", "02d:def"), 2)

    def test_different_length(self):
        # "abc" -> "abcde" needs 2 insertions
        self.assertEqual(levenshtein("abc", "abcde"), 2)

    def test_empty_vs_string(self):
        self.assertEqual(levenshtein("", "abc"), 3)
        self.assertEqual(levenshtein("abc", ""), 3)


class TestRecoverReference(unittest.TestCase):
    """End-to-end test of the recovery decision tree."""

    def test_no_title_match_returns_no_match_outcome(self):
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
                {"title": "Harvest", "reference": "S:d6d9e"},
            ]),
        }

        result = recover_reference(store, "Not In Library", "S:xxxxx")

        self.assertEqual(result.outcome, RecoveryOutcome.NO_MATCH)
        self.assertIsInstance(result, NoTitleMatch)

    def test_unique_title_match_resolves_silently(self):
        store = {
            "res_00001": _group([
                {"title": "Time Out", "reference": "S:3d8cc"},
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
        }

        result = recover_reference(store, "Time Out", "S:3d7cc")

        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertIsInstance(result, UniqueTitleMatch)
        self.assertEqual(result.candidate.reference, "S:3d8cc")
        self.assertEqual(result.candidate.title, "Time Out")
        self.assertEqual(result.candidate.handle, "res_00001")

    def test_unique_title_resolves_even_when_reference_is_wildly_wrong(self):
        """When the title is unique, we trust the title even if the
        submitted reference is nothing like the real one. The LLM only
        emits a given title because it saw it — title-collision with an
        unrelated item is vanishingly rare for this coordinator class."""
        store = {
            "res_00001": _group([
                {"title": "Time Out", "reference": "S:3d8cc"},
            ]),
        }

        result = recover_reference(store, "Time Out", "S:zzzzz")

        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertEqual(result.candidate.reference, "S:3d8cc")

    def test_multi_title_with_clear_fuzzy_winner_resolves(self):
        """Two items share the same title. Fuzzy-rank by Levenshtein
        distance to the submitted reference and pick the strict winner."""
        store = {
            "res_00001": _group([
                {"title": "Greatest Hits", "reference": "S:11fde"},  # Queen
                {"title": "Greatest Hits", "reference": "S:aaaaa"},  # some other
            ]),
        }

        # Submitted ref S:11fff → distance 2 to S:11fde, distance 5 to S:aaaaa
        result = recover_reference(store, "Greatest Hits", "S:11fff")

        self.assertEqual(result.outcome, RecoveryOutcome.FUZZY_WINNER)
        self.assertIsInstance(result, FuzzyTitleWinner)
        self.assertEqual(result.candidate.reference, "S:11fde")
        self.assertEqual(result.candidate.distance, 2)
        self.assertEqual(result.runner_up_distance, 5)

    def test_multi_title_with_tied_distance_returns_ambiguity(self):
        """Two title matches with identical Levenshtein distance to the
        submitted reference — we cannot decide. Coordinator should see
        an ambiguity error rather than an arbitrary silent pick."""
        store = {
            "res_00001": _group([
                {"title": "Greatest Hits", "reference": "S:a1234"},
                {"title": "Greatest Hits", "reference": "S:a1235"},
            ]),
        }

        # Submitted ref S:a1236 is distance 1 from both.
        result = recover_reference(store, "Greatest Hits", "S:a1236")

        self.assertEqual(result.outcome, RecoveryOutcome.AMBIGUOUS_TIE)
        self.assertIsInstance(result, AmbiguousTitleTie)
        self.assertEqual(len(result.tied_candidates), 2)
        tied_refs = {c.reference for c in result.tied_candidates}
        self.assertEqual(tied_refs, {"S:a1234", "S:a1235"})

    def test_searches_all_handles_not_just_most_recent(self):
        """The coordinator can drill through multiple searches in one
        request and then refer back to any of them. We scan every
        cached result, not just the latest."""
        store = {
            "res_00001": _group([
                {"title": "Time Out", "reference": "S:3d8cc"},
            ]),
            "res_00002": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
            "res_00003": _group([
                {"title": "Harvest", "reference": "S:d6d9e"},
            ]),
        }

        # Title that was in the OLDEST search — should still be found.
        result = recover_reference(store, "Time Out", "S:3d7cc")

        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertEqual(result.candidate.reference, "S:3d8cc")
        self.assertEqual(result.candidate.handle, "res_00001")

    def test_ignores_items_without_s_prefix_references(self):
        """Queue references (Q:xxxxx) and other non-S: references
        shouldn't be considered — roon_action fallback only applies to
        library S: references."""
        store = {
            "res_00001": _group([
                {"title": "Time Out", "reference": "Q:ffff1"},  # queue item
                {"title": "Time Out", "reference": "S:3d8cc"},  # library item
            ]),
        }

        result = recover_reference(store, "Time Out", "S:3d7cc")

        # Unique S: match, not ambiguous — the Q: one is filtered out.
        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertEqual(result.candidate.reference, "S:3d8cc")

    def test_ignores_items_missing_title_or_reference(self):
        store = {
            "res_00001": _group([
                {"title": "Time Out", "reference": "S:3d8cc"},
                {"reference": "S:aaaaa"},  # no title
                {"title": "No Ref Item"},   # no reference
                {"title": "", "reference": "S:bbbbb"},  # empty title
            ]),
        }

        result = recover_reference(store, "Time Out", "S:3d7cc")

        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertEqual(result.candidate.reference, "S:3d8cc")

    def test_handles_flat_item_list_payload(self):
        """Not all payloads are the grouped format. A flat list of
        items should also be scanned — be defensive about shape."""
        store = {
            "res_00001": [
                {"title": "Time Out", "reference": "S:3d8cc"},
            ],
        }

        result = recover_reference(store, "Time Out", "S:3d7cc")

        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertEqual(result.candidate.reference, "S:3d8cc")

    def test_handles_non_list_payloads_gracefully(self):
        """web_search may store dicts or other shapes. Non-iterable or
        unexpected shapes must not raise — just skip."""
        store = {
            "res_00001": {"url": "https://example.com"},  # dict payload
            "res_00002": "a string",                       # string payload
            "res_00003": _group([
                {"title": "Time Out", "reference": "S:3d8cc"},
            ]),
        }

        result = recover_reference(store, "Time Out", "S:3d7cc")

        self.assertEqual(result.outcome, RecoveryOutcome.UNIQUE_TITLE)
        self.assertEqual(result.candidate.reference, "S:3d8cc")

    def test_empty_result_store_returns_no_match(self):
        result = recover_reference({}, "Time Out", "S:3d7cc")
        self.assertEqual(result.outcome, RecoveryOutcome.NO_MATCH)

class TestReferenceCandidateMatch(unittest.TestCase):
    def test_dataclass_fields(self):
        c = ReferenceCandidateMatch(
            reference="S:3d8cc",
            title="Time Out",
            handle="res_00001",
        )
        self.assertEqual(c.reference, "S:3d8cc")
        self.assertEqual(c.title, "Time Out")
        self.assertEqual(c.handle, "res_00001")
        self.assertIsNone(c.group)
        self.assertEqual(c.distance, 0)


class TestLookupTitleForReference(unittest.TestCase):
    """Inverse of recover_reference: given a (valid) reference, find the
    title that was associated with it in the result store. Used by the
    title/reference-mismatch check after a reference resolves
    successfully."""

    def test_finds_title_in_flat_group_payload(self):
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
                {"title": "Harvest", "reference": "S:d6d9e"},
            ]),
        }
        self.assertEqual(lookup_title_for_reference(store, "S:80bf1"), "Abbey Road")
        self.assertEqual(lookup_title_for_reference(store, "S:d6d9e"), "Harvest")

    def test_finds_title_across_multiple_handles(self):
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
            "res_00002": _group([
                {"title": "Harvest", "reference": "S:d6d9e"},
            ]),
        }
        self.assertEqual(lookup_title_for_reference(store, "S:80bf1"), "Abbey Road")
        self.assertEqual(lookup_title_for_reference(store, "S:d6d9e"), "Harvest")

    def test_returns_none_when_reference_not_found(self):
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
        }
        self.assertIsNone(lookup_title_for_reference(store, "S:99999"))

    def test_returns_none_for_empty_store(self):
        self.assertIsNone(lookup_title_for_reference({}, "S:80bf1"))

    def test_finds_first_match_when_reference_appears_multiple_times(self):
        """References should be unique within a session, but if duplicates
        somehow appear (e.g. rerun searches), we just take the first match.
        The behaviour should be deterministic, not crash."""
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
            "res_00002": _group([
                {"title": "Abbey Road (Remastered)", "reference": "S:80bf1"},
            ]),
        }
        result = lookup_title_for_reference(store, "S:80bf1")
        self.assertIsNotNone(result)
        self.assertIn(result, {"Abbey Road", "Abbey Road (Remastered)"})


class TestTitlesMatch(unittest.TestCase):
    """Fuzzy title comparison used by the title/reference-mismatch check.

    Tolerates the small transcription variations LLMs make (case,
    whitespace, dropped parenthesised suffixes) without accepting
    genuinely different titles."""

    def test_exact_match(self):
        self.assertTrue(titles_match("Abbey Road", "Abbey Road"))

    def test_case_insensitive(self):
        self.assertTrue(titles_match("ABBEY ROAD", "abbey road"))
        self.assertTrue(titles_match("Abbey Road", "abbey road"))

    def test_whitespace_tolerant(self):
        self.assertTrue(titles_match("  Abbey Road  ", "Abbey Road"))
        self.assertTrue(titles_match("Abbey Road", "Abbey Road  "))

    def test_dropped_parenthesised_suffix(self):
        """A common LLM transcription: 'Title (Remastered)' → 'Title'."""
        self.assertTrue(titles_match("Abbey Road", "Abbey Road (Remastered)"))
        self.assertTrue(titles_match("Karma Police", "Karma Police (2024 Remaster)"))
        self.assertTrue(titles_match("Take Five", "Take Five (Live)"))

    def test_dropped_bracketed_suffix(self):
        self.assertTrue(titles_match("Title", "Title [Live]"))
        self.assertTrue(titles_match("Title", "Title [Bonus Track]"))

    def test_leading_parens_kept(self):
        """Some real titles start with parens, e.g. '(I Can't Get No)
        Satisfaction'. Stripping should be trailing-only."""
        self.assertTrue(titles_match(
            "(I Can't Get No) Satisfaction",
            "(I Can't Get No) Satisfaction",
        ))

    def test_genuinely_different_titles_dont_match(self):
        self.assertFalse(titles_match("Karma Police", "No Surprises"))
        self.assertFalse(titles_match("Abbey Road", "Let It Be"))

    def test_short_titles_dont_collapse_to_match(self):
        """One-letter titles shouldn't all match each other due to threshold
        rounding."""
        self.assertFalse(titles_match("Hey", "Hi"))
        self.assertFalse(titles_match("Yes", "No"))

    def test_minor_typo_within_threshold(self):
        """Single-character typo on a longer title should still match."""
        self.assertTrue(titles_match("The Dark Side of the Moon", "The Dark Side of teh Moon"))

    def test_empty_strings_dont_match(self):
        self.assertFalse(titles_match("", ""))
        self.assertFalse(titles_match("Title", ""))
        self.assertFalse(titles_match("", "Title"))

    def test_dropped_single_disc_track_number_prefix(self):
        """The compact formatter strips 'N. ' from track titles before
        the coordinator sees them; the result store keeps the raw form.
        titles_match must tolerate the asymmetry so a coordinator
        submitting the stripped title still resolves to the stored
        prefixed title."""
        self.assertTrue(titles_match("Beat It", "5. Beat It"))
        self.assertTrue(titles_match(
            "The Girl Is Mine (with Paul McCartney)",
            "3. The Girl Is Mine (with Paul McCartney)",
        ))
        self.assertTrue(titles_match("The Lady in My Life", "9. The Lady in My Life"))

    def test_dropped_multi_disc_track_number_prefix(self):
        """Same asymmetry for the multi-disc 'd-t ' prefix: compact
        formatter strips, store keeps. titles_match must tolerate."""
        self.assertTrue(titles_match("Rock!", "2-1 Rock!"))
        self.assertTrue(titles_match("Side By Side", "1-15 Side By Side"))


class TestLookupReferencesForTitle(unittest.TestCase):
    """Inverse of lookup_title_for_reference: given a title, find every
    reference whose stored title fuzzy-matches it. Drives the richer
    title/reference-mismatch error message — so the LLM gets shown
    both interpretations (which title the ref points to + which ref
    the title points to)."""

    def test_returns_single_match(self):
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
                {"title": "Let It Be", "reference": "S:abc12"},
            ]),
        }
        self.assertEqual(
            lookup_references_for_title(store, "Abbey Road"),
            ["S:80bf1"],
        )

    def test_returns_multiple_matches_in_iteration_order(self):
        store = {
            "res_00001": _group([
                {"title": "Greatest Hits", "reference": "S:aaa11"},
                {"title": "Greatest Hits", "reference": "S:bbb22"},
                {"title": "Other", "reference": "S:ccc33"},
            ]),
        }
        self.assertEqual(
            lookup_references_for_title(store, "Greatest Hits"),
            ["S:aaa11", "S:bbb22"],
        )

    def test_returns_empty_when_title_not_present(self):
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
        }
        self.assertEqual(
            lookup_references_for_title(store, "Not In Library"),
            [],
        )

    def test_empty_store_returns_empty(self):
        self.assertEqual(lookup_references_for_title({}, "Anything"), [])

    def test_uses_fuzzy_title_match(self):
        """Same fuzzy semantics as titles_match — case, whitespace,
        and trailing parenthesised suffixes are tolerated."""
        store = {
            "res_00001": _group([
                {"title": "Abbey Road (Remastered)", "reference": "S:80bf1"},
            ]),
        }
        self.assertEqual(
            lookup_references_for_title(store, "Abbey Road"),
            ["S:80bf1"],
        )
        self.assertEqual(
            lookup_references_for_title(store, "ABBEY ROAD"),
            ["S:80bf1"],
        )

    def test_dedupes_by_reference_across_handles(self):
        """If the same reference appears in multiple handles (e.g.
        rerun searches) we don't list it twice."""
        store = {
            "res_00001": _group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
            "res_00002": _group([
                {"title": "Abbey Road (Remastered)", "reference": "S:80bf1"},
            ]),
        }
        self.assertEqual(
            lookup_references_for_title(store, "Abbey Road"),
            ["S:80bf1"],
        )


if __name__ == "__main__":
    unittest.main()
