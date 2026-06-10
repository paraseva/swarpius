"""Live tests for the Play Artist artist-signature validation.

Spec-driven: these tests describe the validator-visible contract
without referencing internal detection mechanics. Together with the
offline tests in ``test_roon_action_play_artist_guard.py``, they
provide independent validation that the Roon action_list shapes
returned by a real Core match what the reconciler expects.

**Read-only.** Both paths exercise ``get_media_actions``, which walks
the browse tree to find the action_list shape but never drills into
an action item — so no playback ever starts. Action-layer dispatch
and error formatting are pure-string concerns covered offline.

* **Success** — ``intended_item_category="artist"`` against a real
  artist ref returns an action_list whose titles are a subset of the
  artist signature ``{Shuffle, Start Radio}``.
* **Failure** — same intent against an album ref raises
  ``CategoryCorrectionFailed`` with ``intended_category="artist"``.

Run with:
    ./dev pytest tests/test_play_artist_validation_live.py -v -m live_roon
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

_log = logging.getLogger("swarpius.play_artist_validation_live")

pytestmark = pytest.mark.live_roon

ARTIST_SEARCH = os.environ.get("ROON_TEST_ARTIST_A", "")

_ARTIST_ACTION_SIGNATURE = {"Shuffle", "Start Radio"}


class _LiveRoonTestCase(unittest.TestCase):
    REQUIRED_ENV: tuple = ("ROON_TEST_ARTIST_A",)

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
        """Search, drill into *category_title* (e.g. 'Artists',
        'Albums'), return the first item's ``(title, reference)``.
        Skips the test when the category isn't reachable for the
        given search term.
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


class TestArtistIntentMatchesArtistRef(_LiveRoonTestCase):
    """``intended_category='artist'`` against a real artist ref:
    ``get_media_actions`` returns an action_list whose titles are a
    subset of the artist signature. Read-only; no playback starts.
    """

    def test_artist_ref_yields_artist_action_signature(self):
        title, reference = self._first_in_category(ARTIST_SEARCH, "Artists")
        _log.info("get_media_actions(artist=intent) on: %s (%s)", title, reference)

        results, _sk, _levels = self.roon.get_media_actions(
            media_item=RoonCoreItemSummarySchema(
                title=title, reference=reference,
            ),
            intended_item_category="artist",
        )

        self.assertIsNotNone(results, "Expected action_list, got None")
        self.assertEqual(
            results.list.hint if results.list else None,
            "action_list",
            f"Expected action_list, got list={results.list}",
        )
        action_titles = {i.title for i in (results.items or [])}
        self.assertTrue(
            action_titles.issubset(_ARTIST_ACTION_SIGNATURE),
            f"Artist action_list contained non-signature titles: "
            f"{action_titles - _ARTIST_ACTION_SIGNATURE}",
        )


class TestArtistIntentRejectsAlbumRef(_LiveRoonTestCase):
    """``intended_category='artist'`` against an album ref raises
    ``CategoryCorrectionFailed`` from inside ``get_media_actions``
    before any action drill happens. Read-only; no playback starts.
    """

    def test_album_ref_with_artist_intent_raises_correction(self):
        title, reference = self._first_in_category(ARTIST_SEARCH, "Albums")
        _log.info("get_media_actions(artist=intent) on: %s (%s)", title, reference)

        with self.assertRaises(CategoryCorrectionFailed) as ctx:
            self.roon.get_media_actions(
                media_item=RoonCoreItemSummarySchema(
                    title=title, reference=reference,
                ),
                intended_item_category="artist",
            )

        self.assertEqual(ctx.exception.intended_category, "artist")
        self.assertEqual(ctx.exception.category_name, "Artists")


if __name__ == "__main__":
    unittest.main()
