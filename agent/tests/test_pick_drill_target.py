"""Behaviour contract for ``_pick_drill_target``.

The drill-target chooser has five distinct branches (no items,
duplicate, gateway match, gateway mismatch, uniform-group fuzzy
match) and zero direct tests — it's currently exercised only
indirectly through ``get_media_actions``. These tests pin each
branch so the upcoming ReferenceWalker extraction preserves the
contract.
"""

from __future__ import annotations

import unittest

from roon_core.browse import RoonBrowseMixin
from roon_core.browse_session import ItemIdentity, SearchRecipe, StableReference
from roon_core.reference_walker import ReferenceWalker
from roon_core.schemas import (
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)


class _Host(RoonBrowseMixin):
    """Minimal browse facade — the walker only reaches
    ``_duplicate_found`` on the host, which lives on the mixin.
    No api / session_manager / connection state needed."""


_picker = ReferenceWalker(_Host())


def _item(title: str, hint: str | None = None, subtitle: str = "", item_key: str = "") -> RoonCoreItemSchema:
    return RoonCoreItemSchema(
        title=title, subtitle=subtitle, hint=hint,
        item_key=item_key or f"k-{title}",
    )


def _results(items: list[RoonCoreItemSchema], hint: str | None = None) -> RoonCoreResultsSchema:
    return RoonCoreResultsSchema(
        items=items,
        list=RoonCoreListSchema(count=len(items), hint=hint),
    )


def _ref(
    title: str = "Thriller",
    hint: str | None = "action_list",
    subtitle: str | None = None,
) -> StableReference:
    return StableReference(
        ref_id="r1",
        identity=ItemIdentity(title=title, subtitle=subtitle, hint=hint),
        recipe=SearchRecipe(search_string=title),
        cached_item_key="cached",
        roon_session_key="sess",
        item_key_path=[],
    )


class TestEmptyResults(unittest.TestCase):
    def test_no_items_returns_none(self):
        results = _results([])
        self.assertIsNone(_picker._pick_drill_target(results, _ref()))


class TestDuplicateLevel(unittest.TestCase):
    """When the resolver lands on a single-item level whose item
    matches the ref's identity, that single item IS the target —
    drill straight into it."""

    def test_single_matching_item_is_chosen(self):
        results = _results(
            [_item("Thriller", hint="action_list")],
        )
        chosen = _picker._pick_drill_target(results, _ref(title="Thriller", hint="action_list"))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, "Thriller")


class TestGatewayBranch(unittest.TestCase):
    def test_gateway_with_matching_intent_drills_in(self):
        """Gateway item (Play Album) + intent=album → drill into it."""
        results = _results([
            _item("Play Album", hint="Action"),
            _item("Track 1", hint="action_list"),
        ])
        chosen = _picker._pick_drill_target(
            results, _ref(), intended_item_category="album",
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, "Play Album")

    def test_gateway_with_auto_intent_drills_in(self):
        """No specific intent → gateway is the safe drill target."""
        results = _results([
            _item("Play Album", hint="Action"),
            _item("Track 1", hint="action_list"),
        ])
        chosen = _picker._pick_drill_target(
            results, _ref(), intended_item_category="auto",
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, "Play Album")

    def test_gateway_with_mismatched_intent_returns_none(self):
        """Gateway=Play Album but intent=track → don't drill the
        gateway; return None so the caller invokes the reconciler."""
        results = _results([
            _item("Play Album", hint="Action"),
            _item("Track 1", hint="action_list"),
        ])
        chosen = _picker._pick_drill_target(
            results, _ref(), intended_item_category="track",
        )
        self.assertIsNone(chosen)


class TestUniformGroup(unittest.TestCase):
    """When every item shares the same hint (action_list or list),
    we're at a variant-grouping level. ``fuzzy_find`` scores by
    title+subtitle; on a match it wins, otherwise we fall back to
    items[0]."""

    def test_subtitled_identity_picks_best_match(self):
        """When the identity carries a subtitle, fuzzy_find can
        actually score above its default threshold."""
        results = _results([
            _item("Other Track", subtitle="Other Artist", hint="action_list"),
            _item("Thriller", subtitle="Michael Jackson", hint="action_list"),
        ])
        chosen = _picker._pick_drill_target(
            results,
            _ref(title="Thriller", subtitle="Michael Jackson", hint="action_list"),
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, "Thriller")

    def test_subtitleless_identity_falls_back_to_first_item(self):
        """Production-realistic: identities minted from item titles
        often lack subtitle. fuzzy_find's default threshold + 70/30
        weighting means no candidate can score above 70, so the
        ``items[0]`` fallback fires unconditionally."""
        results = _results([
            _item("Other Track", hint="action_list"),
            _item("Thriller", hint="action_list"),
        ])
        chosen = _picker._pick_drill_target(
            results,
            _ref(title="Thriller", hint="action_list"),
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, "Other Track")

    def test_uniform_list_falls_back_to_first_when_no_fuzzy_match(self):
        results = _results([
            _item("Version 1", hint="list"),
            _item("Version 2", hint="list"),
        ])
        chosen = _picker._pick_drill_target(
            results, _ref(title="Thriller", hint="list"),
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, "Version 1")


class TestNoMatchableShape(unittest.TestCase):
    """Items with mixed hints that aren't gateway/duplicate/uniform —
    no safe drill target. Return None and let the caller handle it."""

    def test_mixed_hints_with_no_gateway_returns_none(self):
        results = _results([
            _item("Unrelated", hint="list"),
            _item("Another", hint="action_list"),
        ])
        chosen = _picker._pick_drill_target(
            results, _ref(title="Thriller", hint="action_list"),
        )
        self.assertIsNone(chosen)


if __name__ == "__main__":
    unittest.main()
