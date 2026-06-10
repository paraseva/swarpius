"""Behaviour contract for the full recursion inside
``_correct_via_category_search``.

Existing tests in ``test_complex_shuffle.py::TestReconcileIntendedCategory``
cover the *dispatch* — which correction branch fires for which input
— but stop at the first ``browse_core`` call. Existing tests in
``test_category_correction.py`` cover failure modes (no category, no
match) but use a hand-scripted ``_BrowseHost`` and don't assert on
the full call sequence or ``levels_pushed`` accounting.

This file fills the gap: end-to-end success paths through the public
``reconcile_intended_category`` dispatcher, asserting on the
observable browse-call sequence and the corrected results. Uses
``BrowseFake`` so production code (the recursive correction +
position tracking + ref re-pointing) runs through to a successful
outcome.

The tests will move with ``_correct_via_category_search`` when it
is extracted into a ``CategoryReconciler`` class; the assertions
pin behaviour, not the call site.
"""

from __future__ import annotations

import unittest

try:
    from tests._browse_fake import BrowseFake
except ModuleNotFoundError:
    from _browse_fake import BrowseFake

from roon_core.browse_session import ItemIdentity, SearchRecipe, StableReference
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)


def _track_results(title: str) -> RoonCoreResultsSchema:
    """Build the ``current_results`` shape that triggers the
    track→album correction branch: a wrapper level whose single
    child is an action_list."""
    return RoonCoreResultsSchema(
        items=[RoonCoreItemSchema(title=title, hint="action_list")],
        list=RoonCoreListSchema(count=1, hint=None, title=title),
    )


class TestSuccessfulCategoryCorrection(unittest.TestCase):
    """A track ref + intended=album walks the full recursion:
    search → drill Albums category → drill matched album → return
    its container. Asserts on the observable browse-call sequence
    and the ``(results, session_key, levels_pushed)`` return value."""

    def test_search_then_two_drills_returns_album_container(self):
        fake = BrowseFake()
        fake.register_category_search_chain(
            track_ref_id="trk1",
            title="Thriller",
            intended_category="album",
            album_track_titles=["Wanna Be Startin' Somethin'", "Baby Be Mine"],
        )
        ref = fake.session_manager.refs["trk1"]
        current = _track_results("Thriller")

        result = fake.reconcile_intended_category(
            ref, "album", current, fake._session_key,
        )

        self.assertIsNotNone(result)
        corrected_results, sk, levels_pushed = result
        self.assertEqual(levels_pushed, 2)
        self.assertEqual(sk, fake.session_manager.recovery_session_key)

        # The full sequence: one search + two drills.
        self.assertEqual(len(fake.browse_aux_calls), 3)
        search_call, cat_drill, album_drill = fake.browse_aux_calls
        self.assertTrue(search_call.get("pop_all"))
        self.assertEqual(search_call["input"], "Thriller")
        self.assertIn("Albums", cat_drill["item_key"])
        self.assertIn("match-album", album_drill["item_key"])

        # The corrected container holds the album's tracks (plus the
        # Play Album gateway that production drops before enumeration).
        titles = [i.title for i in corrected_results.items]
        self.assertIn("Wanna Be Startin' Somethin'", titles)
        self.assertIn("Baby Be Mine", titles)


class TestDisambiguationDrillsIntoFirstVersion(unittest.TestCase):
    """When the matched album entry expands into multiple
    ``hint='list'`` children (the "this album exists in N versions"
    Roon shape), the corrector drills once more into the first
    version and that drill's results become the corrected container."""

    def test_all_list_children_trigger_extra_drill(self):
        fake = BrowseFake()
        # Track-ref scaffold (search response, category drill chain)
        fake.register_category_search_chain(
            track_ref_id="trk2",
            title="Hounds of Love",
            intended_category="album",
            album_track_titles=[],  # tracks set up below via re-registration
        )
        # Replace the matched-album drill response with a disambiguation
        # level (all children hint='list' — N versions of the same
        # album), then register what drilling into the first version
        # yields (the actual track list).
        v1_key = "version-1-trk2"
        v2_key = "version-2-trk2"
        fake.register_drill("match-album-trk2", RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Hounds of Love", item_key=v1_key, hint="list"),
                RoonCoreItemSchema(title="Hounds of Love (Remastered)", item_key=v2_key, hint="list"),
            ],
            list=RoonCoreListSchema(count=2, hint="list", title="Hounds of Love"),
        ))
        fake.register_drill(v1_key, RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Running Up That Hill", item_key="t1", hint="action_list"),
                RoonCoreItemSchema(title="Hounds of Love", item_key="t2", hint="action_list"),
            ],
            list=RoonCoreListSchema(count=2, hint="list", title="Hounds of Love"),
        ))

        ref = fake.session_manager.refs["trk2"]
        current = _track_results("Hounds of Love")

        result = fake.reconcile_intended_category(
            ref, "album", current, fake._session_key,
        )

        self.assertIsNotNone(result)
        corrected_results, _sk, levels_pushed = result
        # Search + category drill + matched-album drill + version drill = 4.
        self.assertEqual(len(fake.browse_aux_calls), 4)
        self.assertEqual(levels_pushed, 3)
        # Final container is the first version's track list, not the
        # disambiguation level itself.
        titles = [i.title for i in corrected_results.items]
        self.assertEqual(titles, ["Running Up That Hill", "Hounds of Love"])
        # The ref's cached_item_key has been re-pointed at the first
        # version (the container the caller will drill from).
        self.assertEqual(ref.cached_item_key, v1_key)


class TestPositionPathTrackingThroughRecursion(unittest.TestCase):
    """``_correct_via_category_search`` builds a ``position_path`` from
    each item_key it drills through (when the key has a numeric position
    suffix). The corrected ref's ``item_key_path`` reflects that path so
    child refs minted later walk the right route. Realistic Roon item
    keys carry positions (e.g. ``'1132:3'``); synthetic test keys
    without a position simply contribute nothing, and the chain still
    works."""

    def test_position_path_captures_only_keys_with_positions(self):
        fake = BrowseFake()
        # Register a track ref by hand using colon-separated keys
        # (Roon's real format) so _item_key_position returns the
        # position suffix and position_path captures it.
        ref = StableReference(
            ref_id="trk3",
            identity=ItemIdentity(title="Voices", hint="action_list"),
            recipe=SearchRecipe(search_string="Voices"),
            cached_item_key="100:0",
            roon_session_key=fake._session_key,
            item_key_path=[],
        )
        fake.session_manager.refs["trk3"] = ref

        # Wire up the chain: search returns Albums category at position 5,
        # drilling Albums returns Voices album at position 3, drilling
        # the album returns its tracks.
        cat_key = "200:5"
        match_key = "300:3"
        fake._search_responses["Voices"] = [
            RoonCoreItemSchema(title="Albums", item_key=cat_key, hint="list"),
        ]
        fake.register_drill(cat_key, RoonCoreResultsSchema(
            items=[RoonCoreItemSchema(title="Voices", item_key=match_key, hint="list")],
            list=RoonCoreListSchema(count=1, hint="list", title="Albums"),
        ))
        fake.register_drill(match_key, RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(title="Voices", item_key="400:0", hint="action_list"),
            ],
            list=RoonCoreListSchema(count=1, hint="list", title="Voices"),
        ))

        result = fake.reconcile_intended_category(
            ref, "album", _track_results("Voices"), fake._session_key,
        )
        self.assertIsNotNone(result)

        # The two drilled keys both have positions — the path is
        # ['5', '3'] in drill order (category first, then matched
        # album). The wrapper-action_list drill (the final results
        # level) doesn't contribute because the corrector stops
        # appending after the match drill.
        self.assertEqual(ref.item_key_path, ["5", "3"])
        # The ref now points at the matched album's container key.
        self.assertEqual(ref.cached_item_key, match_key)


if __name__ == "__main__":
    unittest.main()
