"""Live tests for Shuffle's persona handling under the matrix dispatcher.

Spec-driven: assert the coordinator-visible contract against a real
Roon Core, complementing the offline matrix tests in
``test_action_matrix.py``. No playback is started — single-persona
Shuffle is asserted at the ``get_media_actions`` seam (verifying
Roon exposes the Shuffle action in the persona's action_list) so
the test can confirm the matrix dispatch path without triggering
the action. Multi-persona Shuffle rejects without reaching the
dispatch seam at all.

Run with:
    ./dev pytest tests/test_shuffle_artist_live.py -v -m live_roon
"""

import asyncio
import logging
import os
import unittest

import pytest

from roon_core.schemas import RoonCoreItemSummarySchema
from tools.roon_action import (
    RoonActionTool,
    RoonActionToolConfig,
    RoonActionToolInputSchema,
)
from tools.roon_search import (
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
)

_log = logging.getLogger("swarpius.shuffle_artist_live")

pytestmark = pytest.mark.live_roon

ARTIST_SEARCH_A = os.environ.get("ROON_TEST_ARTIST_A", "")
ARTIST_SEARCH_B = os.environ.get("ROON_TEST_ARTIST_B", "")

_PERSONA_ACTION_SIGNATURE = {"Shuffle", "Start Radio"}


class _LiveRoonTestCase(unittest.TestCase):
    REQUIRED_ENV: tuple = ("ROON_TEST_ARTIST_A", "ROON_TEST_ARTIST_B")

    @classmethod
    def setUpClass(cls):
        from tests.conftest import get_live_roon
        cls.roon = get_live_roon()
        cls.search_tool = RoonSearchTool(
            RoonSearchToolConfig(roon_connection=cls.roon),
        )
        cls.action_tool = RoonActionTool(
            RoonActionToolConfig(resolve_zone=lambda z: z),
        )
        cls.action_tool.roon_connection = cls.roon

    def setUp(self):
        missing = [n for n in self.REQUIRED_ENV if not os.environ.get(n)]
        if missing:
            self.skipTest(
                f"Set {', '.join(missing)} in agent/.env.test "
                f"(see .env.test.template)",
            )
        if not self.roon.wait_for_connection(timeout=30):
            self.skipTest("Roon connection not available")

    def _first_artist_ref(self, search_term):
        """Search, drill into the Artists category, return the first
        artist's (title, reference). Skips the test if not reachable —
        e.g. the library has no match for *search_term*.
        """
        result = asyncio.run(self.search_tool.run_async(
            RoonSearchToolInputSchema(
                operation="new_search", search_string=search_term,
            )
        ))
        if not result.groups:
            self.skipTest(f"No results for '{search_term}'")

        artists_ref = None
        for group in result.groups:
            for item in group.items:
                if item.title == "Artists":
                    artists_ref = item.reference
                    break
            if artists_ref:
                break
        if not artists_ref:
            self.skipTest(f"No 'Artists' category for '{search_term}'")

        drill = asyncio.run(self.search_tool.run_async(
            RoonSearchToolInputSchema(
                operation="drill_down_reference", reference=artists_ref,
            )
        ))
        if not drill.groups or not drill.groups[0].items:
            self.skipTest(f"No artists in Artists category for '{search_term}'")

        first = drill.groups[0].items[0]
        return first.title, first.reference


class TestSinglePersonaShuffleResolvesToNativeAction(_LiveRoonTestCase):
    """Single-persona Shuffle dispatches Roon's native action. To avoid
    starting playback, assert at the ``get_media_actions`` seam: the
    walker reaches the persona's terminal action_list and Shuffle is
    among the available actions — proving the matrix would dispatch
    Shuffle when ``run_async`` is called."""

    def test_artist_action_list_contains_shuffle(self):
        title, reference = self._first_artist_ref(ARTIST_SEARCH_A)
        _log.info("artist seam probe: %s (%s)", title, reference)

        results, _sk, _levels = self.roon.get_media_actions(
            media_item=RoonCoreItemSummarySchema(
                title=title, reference=reference,
            ),
        )

        self.assertIsNotNone(
            results, f"Expected action_list, got None for {reference}",
        )
        self.assertEqual(
            results.list.hint if results.list else None, "action_list",
            f"Expected terminal action_list, got list={results.list}",
        )
        action_titles = {i.title for i in (results.items or [])}
        self.assertIn(
            "Shuffle", action_titles,
            f"Persona action_list missing Shuffle: {action_titles}",
        )
        self.assertTrue(
            action_titles.issubset(_PERSONA_ACTION_SIGNATURE),
            f"Persona action_list contained non-persona titles: "
            f"{action_titles - _PERSONA_ACTION_SIGNATURE}",
        )


class TestMultiPersonaShuffleRejects(_LiveRoonTestCase):
    """Shuffle with multiple persona refs rejects the whole call
    before any Roon action runs — all refs surface in the structured
    errors so the coordinator can drill into Albums for each and
    re-submit. No playback starts (because no action dispatches)."""

    def test_shuffle_multiple_artists_rejects_with_both_refs(self):
        title_a, reference_a = self._first_artist_ref(ARTIST_SEARCH_A)
        title_b, reference_b = self._first_artist_ref(ARTIST_SEARCH_B)
        if reference_a == reference_b:
            self.skipTest(
                "Both searches resolved to the same artist; "
                "need two distinct artists",
            )

        _log.info(
            "Multi-artist Shuffle: %s (%s) + %s (%s)",
            title_a, reference_a, title_b, reference_b,
        )

        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                RoonCoreItemSummarySchema(title=title_a, reference=reference_a),
                RoonCoreItemSummarySchema(title=title_b, reference=reference_b),
            ],
        )
        output = asyncio.run(self.action_tool.run_async(params))

        self.assertIn("FAILED", output.result)
        self.assertIsNotNone(
            output.errors,
            f"Expected structured errors, got: {output.result}",
        )
        combined_refs: set[str] = set()
        for e in output.errors:
            combined_refs.update(e.refs)
        self.assertIn(
            reference_a, combined_refs,
            f"Artist A ref missing from errors: {output.errors}",
        )
        self.assertIn(
            reference_b, combined_refs,
            f"Artist B ref missing from errors: {output.errors}",
        )


if __name__ == "__main__":
    unittest.main()
