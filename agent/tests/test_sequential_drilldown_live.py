"""Live test: verify Roon allows drilling into sibling item_keys
without popping levels first.

This validates a key assumption for parallel tool call support:
if the LLM requests drill-downs into two albums from the same
search session, can we execute them sequentially on the same
session without needing to pop back to the album list between them?

Run with:
    ./dev pytest tests/test_sequential_drilldown_live.py -v -m live_roon
"""

import logging
import os
import unittest

import pytest

from tests.conftest import get_live_roon

_log = logging.getLogger("swarpius.browse.sequential_drilldown_test")

pytestmark = pytest.mark.live_roon

SEARCH_TERM = os.environ.get("ROON_TEST_SEARCH_A", "")


class TestSequentialSiblingDrilldowns(unittest.TestCase):
    """Verify that Roon allows drilling into sibling item_keys
    after already having drilled into one, without popping first."""

    @classmethod
    def setUpClass(cls):
        cls.roon = get_live_roon()

    def setUp(self):
        if not SEARCH_TERM:
            self.skipTest(
                "Set ROON_TEST_SEARCH_A in agent/.env.test "
                "(see .env.test.template)",
            )
        if not self.roon.wait_for_connection(timeout=30):
            self.skipTest("Roon connection not available")

    def test_drill_two_sibling_albums_same_session(self):
        """Search -> Albums category -> drill album A -> drill album B
        (without popping) -> verify both return valid track lists."""
        sm = self.roon.session_manager
        sk = sm.new_search_session()

        # 1. Search
        results = self.roon.browse_core(
            {"pop_all": True, "input": SEARCH_TERM},
            session_key=sk,
        )
        if not results.items:
            self.skipTest(f"No results for '{SEARCH_TERM}'")

        # 2. Find and drill into "Albums" category
        albums_cat = self.roon.find_item_by_field(
            results.items, "title", "Albums",
        )
        if not albums_cat:
            self.skipTest(f"No 'Albums' category for '{SEARCH_TERM}'")

        album_list = self.roon._nav_drill(albums_cat.item_key, sk)
        if len(album_list.items) < 2:
            self.skipTest(f"Need >= 2 albums for '{SEARCH_TERM}', got {len(album_list.items)}")

        album_a = album_list.items[0]
        album_b = album_list.items[1]

        _log.info(
            "Drilling into album A: '%s' (key=%s) and album B: '%s' (key=%s)",
            album_a.title, album_a.item_key,
            album_b.title, album_b.item_key,
        )

        # 3. Drill into album A — expect gateway level with album title
        gateway_a = self.roon._nav_drill(album_a.item_key, sk)
        self.assertTrue(
            gateway_a.items,
            f"No items returned for album A '{album_a.title}'",
        )
        _log.info(
            "Album A '%s': gateway has %d items, first: '%s'",
            album_a.title, len(gateway_a.items), gateway_a.items[0].title,
        )

        # Verify gateway content matches album A (not album B)
        self.assertIn(
            album_a.title, gateway_a.items[0].title,
            f"Drill into album A returned wrong content: "
            f"expected '{album_a.title}' in gateway, got '{gateway_a.items[0].title}'",
        )

        # Drill one more level to get actual tracks for album A
        tracks_a = self.roon._nav_drill(gateway_a.items[0].item_key, sk)
        self.assertTrue(tracks_a.items, f"No tracks for album A '{album_a.title}'")
        _log.info(
            "Album A tracks: %s",
            [i.title for i in tracks_a.items[:5]],
        )

        # 4. WITHOUT popping, drill into album B using its item_key
        #    from the album list level
        gateway_b = self.roon._nav_drill(album_b.item_key, sk)
        self.assertTrue(
            gateway_b.items,
            f"No items returned for album B '{album_b.title}'",
        )
        _log.info(
            "Album B '%s': gateway has %d items, first: '%s'",
            album_b.title, len(gateway_b.items), gateway_b.items[0].title,
        )

        # Verify gateway content matches album B (not album A)
        self.assertIn(
            album_b.title, gateway_b.items[0].title,
            f"Drill into album B returned wrong content: "
            f"expected '{album_b.title}' in gateway, got '{gateway_b.items[0].title}'",
        )

        # Drill one more level to get actual tracks for album B
        tracks_b = self.roon._nav_drill(gateway_b.items[0].item_key, sk)
        self.assertTrue(tracks_b.items, f"No tracks for album B '{album_b.title}'")
        _log.info(
            "Album B tracks: %s",
            [i.title for i in tracks_b.items[:5]],
        )

        # 5. Verify we got different track lists
        track_titles_a = {i.title for i in tracks_a.items}
        track_titles_b = {i.title for i in tracks_b.items}

        if album_a.title != album_b.title:
            self.assertNotEqual(
                track_titles_a, track_titles_b,
                "Both albums returned identical track lists — "
                "second drill may have returned stale data from first",
            )
