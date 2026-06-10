"""Live tests for composer + work intent validation.

Read-only — both walks exercise ``get_media_actions``, which navigates
the browse tree to find the action_list shape but never drills into an
action item, so no playback ever starts.

Composer (persona family, action_list signature ``{Shuffle, Start
Radio}``):

* Composer ref + ``intent="composer"`` → action_list with persona
  signature.
* Album ref + ``intent="composer"`` → ``CategoryCorrectionFailed``.

Work (container family, action_list signature shared with album, so
validation discriminates at the gateway level):

* Work ref + ``intent="work"`` → action_list reached after walking the
  ``Play Work`` gateway.
* Album ref + ``intent="work"`` → ``CategoryCorrectionFailed`` raised
  at the gateway level.

Run with:
    ./dev pytest tests/test_composer_work_validation_live.py -v -m live_roon
"""

import asyncio
import logging
import os
import unittest

import pytest

from app.exceptions import CategoryCorrectionFailed
from roon_core.schemas import RoonCoreItemSummarySchema
from tools.roon_search import (
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
)

_log = logging.getLogger("swarpius.composer_work_validation_live")

pytestmark = pytest.mark.live_roon

# Mozart: dense in composers in any classical-leaning library, and
# the Composers category drill yields a deterministic top result
# (Wolfgang Amadeus Mozart) for this query.
CLASSICAL_SEARCH = os.environ.get("ROON_TEST_SEARCH_B", "")

# Works are different: the search-term-to-Work-entry relationship is
# loose (matches by composer / title / performer metadata), so a
# bare composer name like "Mozart" doesn't reliably pin a single
# top Work. For the Work-intent tests we need a narrower query that
# deterministically returns a Mozart-composed Work as the first
# Works-category result, so the resolver is exercised against a
# real gateway+recordings shape rather than whatever ranking churn
# Roon happens to produce on a given day.
WORK_SEARCH = os.environ.get("ROON_TEST_WORK_SEARCH", "")

_PERSONA_ACTION_SIGNATURE = {"Shuffle", "Start Radio"}


class _LiveRoonTestCase(unittest.TestCase):
    REQUIRED_ENV: tuple = ("ROON_TEST_SEARCH_B",)

    @classmethod
    def setUpClass(cls):
        from tests.conftest import get_live_roon
        cls.roon = get_live_roon()
        cls.search_tool = RoonSearchTool(
            RoonSearchToolConfig(roon_connection=cls.roon),
        )

    def setUp(self):
        missing = [n for n in self.REQUIRED_ENV if not os.environ.get(n)]
        if missing:
            self.skipTest(
                f"Set {', '.join(missing)} in agent/.env.test "
                f"(see .env.test.template)",
            )
        if not self.roon.wait_for_connection(timeout=30):
            self.skipTest("Roon connection not available")

    def _first_in_category(self, search_term, category_title):
        """Search, drill into *category_title* (e.g. 'Composers',
        'Works', 'Albums'), return the first item's
        ``(title, reference)``. Skips when the category isn't
        reachable for the given search term.
        """
        result = asyncio.run(self.search_tool.run_async(
            RoonSearchToolInputSchema(
                operation="new_search", search_string=search_term,
            )
        ))
        if not result.groups:
            self.skipTest(f"No results for '{search_term}'")

        category_ref = None
        for group in result.groups:
            for item in group.items:
                if item.title == category_title:
                    category_ref = item.reference
                    break
            if category_ref:
                break
        if not category_ref:
            self.skipTest(
                f"No '{category_title}' category for '{search_term}'",
            )

        drill = asyncio.run(self.search_tool.run_async(
            RoonSearchToolInputSchema(
                operation="drill_down_reference", reference=category_ref,
            )
        ))
        if not drill.groups or not drill.groups[0].items:
            self.skipTest(
                f"No items in '{category_title}' for '{search_term}'",
            )

        first = drill.groups[0].items[0]
        return first.title, first.reference


class TestComposerIntentMatchesComposerRef(_LiveRoonTestCase):
    """``intended_category="composer"`` against a real composer ref:
    walk reaches the persona terminal action_list."""

    def test_composer_ref_yields_persona_action_signature(self):
        title, reference = self._first_in_category(CLASSICAL_SEARCH, "Composers")
        _log.info("composer probe: %s (%s)", title, reference)

        results, _sk, _levels = self.roon.get_media_actions(
            media_item=RoonCoreItemSummarySchema(
                title=title, reference=reference,
            ),
            intended_item_category="composer",
        )

        self.assertIsNotNone(results, "Expected action_list, got None")
        self.assertEqual(
            results.list.hint if results.list else None,
            "action_list",
            f"Expected action_list, got list={results.list}",
        )
        action_titles = {i.title for i in (results.items or [])}
        self.assertTrue(
            action_titles.issubset(_PERSONA_ACTION_SIGNATURE),
            f"Composer action_list contained non-persona titles: "
            f"{action_titles - _PERSONA_ACTION_SIGNATURE}",
        )


class TestComposerIntentRejectsAlbumRef(_LiveRoonTestCase):
    """``intended_category="composer"`` against an album ref raises
    ``CategoryCorrectionFailed`` from inside ``get_media_actions``
    before any action drill happens."""

    def test_album_ref_with_composer_intent_raises_correction(self):
        title, reference = self._first_in_category(CLASSICAL_SEARCH, "Albums")
        _log.info("composer-on-album probe: %s (%s)", title, reference)

        with self.assertRaises(CategoryCorrectionFailed) as ctx:
            self.roon.get_media_actions(
                media_item=RoonCoreItemSummarySchema(
                    title=title, reference=reference,
                ),
                intended_item_category="composer",
            )

        self.assertEqual(ctx.exception.intended_category, "composer")
        self.assertEqual(ctx.exception.category_name, "Composers")


class TestWorkIntentMatchesWorkRef(_LiveRoonTestCase):
    """``intended_category="work"`` against a real work ref: walk
    progresses through the Play Work gateway and reaches the terminal
    action_list."""

    REQUIRED_ENV = ("ROON_TEST_SEARCH_B", "ROON_TEST_WORK_SEARCH")

    def test_work_ref_yields_action_list(self):
        title, reference = self._first_in_category(WORK_SEARCH, "Works")
        _log.info("work probe: %s (%s)", title, reference)

        results, _sk, _levels = self.roon.get_media_actions(
            media_item=RoonCoreItemSummarySchema(
                title=title, reference=reference,
            ),
            intended_item_category="work",
        )

        self.assertIsNotNone(results, "Expected action_list, got None")
        self.assertEqual(
            results.list.hint if results.list else None,
            "action_list",
            f"Expected action_list, got list={results.list}",
        )
        # Work shares its terminal signature with Album — assert at
        # least one playable action is present.
        action_titles = {i.title for i in (results.items or [])}
        self.assertTrue(
            action_titles & {"Play Now", "Add Next", "Queue", "Start Radio"},
            f"Work action_list missing playable actions: {action_titles}",
        )


class TestWorkIntentRejectsAlbumRef(_LiveRoonTestCase):
    """``intended_category="work"`` against an album ref raises
    ``CategoryCorrectionFailed`` at the gateway level (the gateway is
    Play Album, not Play Work)."""

    def test_album_ref_with_work_intent_raises_correction(self):
        title, reference = self._first_in_category(CLASSICAL_SEARCH, "Albums")
        _log.info("work-on-album probe: %s (%s)", title, reference)

        with self.assertRaises(CategoryCorrectionFailed) as ctx:
            self.roon.get_media_actions(
                media_item=RoonCoreItemSummarySchema(
                    title=title, reference=reference,
                ),
                intended_item_category="work",
            )

        self.assertEqual(ctx.exception.intended_category, "work")
        self.assertEqual(ctx.exception.category_name, "Works")


if __name__ == "__main__":
    unittest.main()
