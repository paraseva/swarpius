"""Live shape probes for action dispatch.

Pins the drill-response shapes the matrix-driven dispatcher relies on,
against measured Roon behaviour rather than assumed behaviour. Each probe
does search + drill only — no action execution, no playback side-effects.

Probes cover:

* Persona-with-children overview shape (artist with reachable albums).
* Persona-without-children gateway shape (artist with zero reachable
  albums — degenerate case).
* Composer overview shape (persona family, parallels artist).
* Work drill shape (container family, parallels album).
* Playlist drill shape (container family).
* Album drill shape (container family — baseline).
* Track drill shape (leaf — baseline).
* "Not Found" recording presence in a work's recordings list (when
  reachable).

The duplicate-wrapper level probes (album + work) need search strings
the test user hasn't pre-configured; they skip with a hint pointing at
the env var to set.

Run with ``-s`` so the shape dumps print to stdout:

    ./dev pytest tests/test_action_shape_probe_live.py -v -s -m live_roon
"""

import asyncio
import logging
import os
import unittest

import pytest
import yaml

from tools.roon_search import (
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
)

_log = logging.getLogger("swarpius.action_shape_probe_live")

pytestmark = pytest.mark.live_roon


ARTIST_WITHOUT_ALBUMS = os.environ.get("ROON_TEST_ARTIST_A", "")
# An artist the user has reachable albums for — exercises the
# multi-item overview shape the offline fakes don't yet reproduce.
ARTIST_WITH_ALBUMS_FALLBACK = "Judas Priest"
ARTIST_WITH_ALBUMS = os.environ.get(
    "ROON_TEST_ARTIST_WITH_ALBUMS", ARTIST_WITH_ALBUMS_FALLBACK,
)
COMPOSER_SEARCH = os.environ.get("ROON_TEST_SEARCH_B", "")
WORK_SEARCH = os.environ.get("ROON_TEST_WORK_SEARCH", "")
PLAYLIST_NAME = os.environ.get("ROON_TEST_PLAYLIST", "")
ALBUM_NAME = os.environ.get("ROON_TEST_ALBUM", "")
TRACK_SEARCH = os.environ.get("ROON_TEST_GATEWAY_TRACK_SEARCH", "")
TRACK_TITLE = os.environ.get("ROON_TEST_GATEWAY_TRACK_TITLE", "")

# Duplicate-wrapper probes need search strings the user hasn't yet
# provided. The probe skips with a hint if these aren't set.
DUPLICATE_WRAPPER_ALBUM = os.environ.get(
    "ROON_TEST_DUPLICATE_WRAPPER_ALBUM", "",
)
DUPLICATE_WRAPPER_WORK = os.environ.get(
    "ROON_TEST_DUPLICATE_WRAPPER_WORK", "",
)


def _dump_shape(label: str, results) -> None:
    """Print the drill-response shape so the probe output can be
    eyeballed and pasted into the design doc. yaml.safe_dump keeps the
    output diff-friendly across runs."""
    payload = {
        "label": label,
        "list_title": results.list.title if results.list else None,
        "list_hint": results.list.hint if results.list else None,
        "list_subtitle": getattr(results.list, "subtitle", None) if results.list else None,
        "list_image_key": getattr(results.list, "image_key", None) if results.list else None,
        "item_count": len(results.items or []),
        "items": [
            {
                "title": i.title,
                "subtitle": i.subtitle,
                "hint": i.hint,
                "image_key": i.image_key,
                "item_key": i.item_key,
            }
            for i in (results.items or [])
        ],
    }
    print(f"\n===== SHAPE: {label} =====")
    print(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False))


class _LiveProbe(unittest.TestCase):
    REQUIRED_ENV: tuple = ()

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

    def _search_top_results(self, search_term):
        result = asyncio.run(self.search_tool.run_async(
            RoonSearchToolInputSchema(
                operation="new_search", search_string=search_term,
            )
        ))
        if not result.groups:
            self.skipTest(f"No results for '{search_term}'")
        return result

    def _first_top_level_item(self, search_term, expected_title=None):
        """Return (title, reference) of the first search-result item
        (the top-level tile). When ``expected_title`` is set, skip if
        the first item's title doesn't match (defensive against
        ranking drift)."""
        result = self._search_top_results(search_term)
        first = result.groups[0].items[0]
        if expected_title and first.title.lower() != expected_title.lower():
            self.skipTest(
                f"Top result for '{search_term}' is "
                f"'{first.title}', expected '{expected_title}'",
            )
        return first.title, first.reference

    def _items_in_category(self, search_term, category_title):
        """Drill into *category_title* and return the list of items
        inside (full, not just the first)."""
        result = self._search_top_results(search_term)
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
        return drill.groups[0].items

    def _first_in_category(self, search_term, category_title):
        """First item in the category drill."""
        items = self._items_in_category(search_term, category_title)
        return items[0].title, items[0].reference

    def _last_in_category(self, search_term, category_title):
        """Last item in the category drill — useful for probing entries
        Roon ranks lowest (often the degenerate / 'Not Found' shapes)."""
        items = self._items_in_category(search_term, category_title)
        return items[-1].title, items[-1].reference

    def _resolve_and_drill(self, reference):
        """Resolve a search reference and drill into it; return the
        drill response. Bypasses the action layer so we only see the
        raw shape."""
        ref = self.roon.resolve_reference(reference)
        self.assertIsNotNone(ref, f"Failed to resolve '{reference}'")
        result = self.roon._nav_drill(
            ref.cached_item_key, ref.roon_session_key, update_current=False,
        )
        self.roon._nav_reset_to_root(ref.roon_session_key)
        return result


class TestPersonaWithChildrenShape(_LiveProbe):
    """An artist the user has reachable albums for. Expected shape:
    list_hint=null, items[0] = ('Play Artist', action_list), siblings
    = album entries (hint='list')."""

    def test_top_level_artist_tile_drill(self):
        title, reference = self._first_top_level_item(ARTIST_WITH_ALBUMS)
        _log.info(
            "artist-with-albums top-level tile: %s (%s)", title, reference,
        )
        result = self._resolve_and_drill(reference)
        _dump_shape(f"persona-with-children TOP-LEVEL: {title}", result)

        self.assertIsNotNone(result.items)
        self.assertGreaterEqual(
            len(result.items), 2,
            "Expected multi-item overview (gateway + ≥1 child)",
        )
        self.assertEqual(result.items[0].title, "Play Artist")
        self.assertEqual(result.items[0].hint, "action_list")
        sibling_hints = {i.hint for i in result.items[1:]}
        self.assertIn(
            "list", sibling_hints,
            "Expected at least one container-typed (hint='list') sibling",
        )

    def test_drilled_into_artists_subcategory(self):
        title, reference = self._first_in_category(
            ARTIST_WITH_ALBUMS, "Artists",
        )
        _log.info(
            "artist-with-albums via Artists drill: %s (%s)", title, reference,
        )
        result = self._resolve_and_drill(reference)
        _dump_shape(f"persona-with-children VIA-ARTISTS: {title}", result)

        # The library-content insight: shape depends on whether the
        # artist has reachable albums, not on which navigation path got
        # us there. Both top-level and subcategory paths should produce
        # the same multi-item shape for the same artist.
        self.assertGreaterEqual(len(result.items), 2)


class TestPersonaWithoutChildrenShape(_LiveProbe):
    REQUIRED_ENV = ("ROON_TEST_ARTIST_A",)

    def test_artist_without_albums_yields_single_item_gateway(self):
        title, reference = self._first_in_category(
            ARTIST_WITHOUT_ALBUMS, "Artists",
        )
        _log.info(
            "artist-without-albums: %s (%s)", title, reference,
        )
        result = self._resolve_and_drill(reference)
        _dump_shape(f"persona-without-children: {title}", result)

        self.assertEqual(
            len(result.items or []), 1,
            "Expected single-item gateway shape",
        )
        self.assertEqual(result.items[0].title, "Play Artist")
        self.assertEqual(result.items[0].hint, "action_list")


class TestComposerShape(_LiveProbe):
    REQUIRED_ENV = ("ROON_TEST_SEARCH_B",)

    def test_composer_drill_shape(self):
        title, reference = self._first_in_category(
            COMPOSER_SEARCH, "Composers",
        )
        _log.info("composer: %s (%s)", title, reference)
        result = self._resolve_and_drill(reference)
        _dump_shape(f"composer: {title}", result)

        # Composer should match persona shape: gateway + children
        # (works), or single-item gateway if no reachable works.
        self.assertIsNotNone(result.items)
        self.assertGreaterEqual(len(result.items), 1)
        self.assertEqual(result.items[0].title, "Play Composer")


class TestWorkShape(_LiveProbe):
    REQUIRED_ENV = ("ROON_TEST_WORK_SEARCH",)

    def test_work_drill_shape(self):
        title, reference = self._first_in_category(WORK_SEARCH, "Works")
        _log.info("work: %s (%s)", title, reference)
        result = self._resolve_and_drill(reference)
        _dump_shape(f"work: {title}", result)

        # Work should match container shape: 'Play Work' gateway + leaf
        # recordings (hint='action_list').
        self.assertIsNotNone(result.items)
        self.assertEqual(result.items[0].title, "Play Work")
        recording_hints = {i.hint for i in result.items[1:]}
        if recording_hints:
            # Recordings should be leaves. Logged for analysis even
            # when assertion holds; surfaces "Not Found" entries too.
            self.assertEqual(
                recording_hints, {"action_list"},
                f"Expected all recordings to be action_list leaves, "
                f"got hints {recording_hints}",
            )

    def test_work_recordings_for_not_found_entries(self):
        """Drill the last work in the Works subcategory — user reports
        this surfaces the 'Not Found' recording pattern (degenerate
        entries with subtitle/image_key both null), needed to pin the
        filter shape."""
        if not DUPLICATE_WRAPPER_WORK:
            self.skipTest(
                "Set ROON_TEST_DUPLICATE_WRAPPER_WORK to a search whose "
                "Works subcategory last entry exposes 'Not Found' "
                "recordings.",
            )
        title, reference = self._last_in_category(
            DUPLICATE_WRAPPER_WORK, "Works",
        )
        _log.info("not-found probe via last work: %s (%s)", title, reference)
        result = self._resolve_and_drill(reference)
        _dump_shape(f"work (last in Works): {title}", result)

        not_found_entries = [
            {"title": i.title, "subtitle": i.subtitle, "image_key": i.image_key}
            for i in (result.items or [])
            if i.title == "Not Found"
        ]
        print(
            f"\n===== NOT FOUND under work: "
            f"{len(not_found_entries)} entries =====",
        )
        if not_found_entries:
            print(yaml.safe_dump(not_found_entries, sort_keys=False))


class TestPlaylistShape(_LiveProbe):
    REQUIRED_ENV = ("ROON_TEST_PLAYLIST",)

    def test_playlist_drill_shape(self):
        # Playlists surface via the Playlists subcategory; the
        # PLAYLIST env var is the playlist's title.
        title, reference = self._first_in_category(
            PLAYLIST_NAME, "Playlists",
        )
        _log.info("playlist: %s (%s)", title, reference)
        result = self._resolve_and_drill(reference)
        _dump_shape(f"playlist: {title}", result)

        self.assertIsNotNone(result.items)
        self.assertEqual(result.items[0].title, "Play Playlist")
        child_hints = {i.hint for i in result.items[1:]}
        if child_hints:
            self.assertEqual(
                child_hints, {"action_list"},
                f"Expected playlist children to be action_list leaves, "
                f"got hints {child_hints}",
            )

    # NOTE: don't add a probe that drills into "Play Playlist" (or any
    # other "Play <category>" gateway) live. Drilling a hint="action_list"
    # item is nominally a navigation per Roon's contract, but in practice
    # it has caused playback side-effects in this user's setup. Verify
    # the gateway action_list contents offline via BrowseFake instead.


SINGLE_VERSION_ALBUM_SEARCH = os.environ.get(
    "ROON_TEST_TRACK_ALBUM_SEARCH", "",
)


class TestAlbumShape(_LiveProbe):
    """Album reached via the Albums subcategory. Roon may wrap these
    in a duplicate top-item level before reaching the
    'Play Album + leaf tracks' content. The probe does ONE drill and
    asserts that we landed on either content directly, OR a duplicate
    wrapper. Traversing the chain in live risks side-effects, so the
    matrix's iterative drill is verified offline via BrowseFake's
    register_container_with_duplicate_wrapper."""

    REQUIRED_ENV = ("ROON_TEST_TRACK_ALBUM_SEARCH",)

    def test_album_drill_shape(self):
        title, reference = self._first_in_category(
            SINGLE_VERSION_ALBUM_SEARCH, "Albums",
        )
        _log.info("album: %s (%s)", title, reference)
        result = self._resolve_and_drill(reference)
        _dump_shape(f"album: {title}", result)

        self.assertIsNotNone(result.items)
        detection = _top_item_is_duplicate(result)
        first_title = result.items[0].title
        # Either: at content (Play Album gateway), OR at a wrapper
        # whose top item duplicates parent metadata. The matrix
        # consumes the wrapper transparently — we just confirm Roon
        # produces one of these two shapes here.
        self.assertTrue(
            first_title == "Play Album" or detection["is_duplicate"],
            f"Album drill didn't land on content or a duplicate wrapper: "
            f"items[0].title={first_title!r}, detection={detection}",
        )


class TestTrackShape(_LiveProbe):
    REQUIRED_ENV = ("ROON_TEST_GATEWAY_TRACK_SEARCH",)

    def test_track_drill_shape(self):
        title, reference = self._first_in_category(TRACK_SEARCH, "Tracks")
        _log.info("track: %s (%s)", title, reference)
        result = self._resolve_and_drill(reference)
        _dump_shape(f"track: {title}", result)

        # Track is a leaf — drill yields action_list directly (or a
        # wrapper around one). Both shapes are documented; this probe
        # tells us which is current.
        if result.list and result.list.hint == "action_list":
            action_titles = {i.title for i in (result.items or [])}
            self.assertTrue(
                action_titles.issuperset({"Play Now", "Queue"}),
                f"Track action_list missing core verbs: {action_titles}",
            )


def _top_item_is_duplicate(result) -> dict:
    """Detection per the agreed rule: items[0]'s title/subtitle/image_key
    all match the parent list's same fields. Length-agnostic — covers
    both single-wrapper and multi-version cases under one rule."""
    list_meta = result.list
    if not result.items or not list_meta:
        return {"is_duplicate": False, "reason": "no items or no list metadata"}
    top = result.items[0]
    return {
        "is_duplicate": (
            list_meta.title == top.title
            and getattr(list_meta, "subtitle", None) == top.subtitle
            and getattr(list_meta, "image_key", None) == top.image_key
        ),
        "title_match": list_meta.title == top.title,
        "subtitle_match": getattr(list_meta, "subtitle", None) == top.subtitle,
        "image_key_match": getattr(list_meta, "image_key", None) == top.image_key,
        "item_count": len(result.items),
    }


class TestDuplicateWrapperLevel(_LiveProbe):
    """Confirms Roon actually produces the duplicate-wrapper shape the
    matrix is designed to consume. Single-drill detection only — we
    don't drill past the wrapper in live (side-effect risk per
    feedback_live_tests_no_side_effects). The traversal logic itself
    is tested offline via BrowseFake's
    register_container_with_duplicate_wrapper."""

    def test_duplicate_wrapper_album_detected(self):
        if not DUPLICATE_WRAPPER_ALBUM:
            self.skipTest(
                "Set ROON_TEST_DUPLICATE_WRAPPER_ALBUM in agent/.env.test",
            )
        title, reference = self._first_in_category(
            DUPLICATE_WRAPPER_ALBUM, "Albums",
        )
        result = self._resolve_and_drill(reference)
        _dump_shape(f"duplicate-wrapper album: {title}", result)
        detection = _top_item_is_duplicate(result)
        print("\n===== TOP-ITEM-DUPLICATE DETECTION =====")
        print(yaml.safe_dump(detection, sort_keys=False))
        self.assertTrue(
            detection["is_duplicate"],
            f"Expected items[0] to duplicate parent metadata: {detection}",
        )

    def test_duplicate_wrapper_work_shape_dump(self):
        if not DUPLICATE_WRAPPER_WORK:
            self.skipTest(
                "Set ROON_TEST_DUPLICATE_WRAPPER_WORK in agent/.env.test",
            )
        title, reference = self._first_in_category(
            DUPLICATE_WRAPPER_WORK, "Works",
        )
        result = self._resolve_and_drill(reference)
        _dump_shape(f"work: {title}", result)
        # Per the live probe baseline, the Eine Kleine Nachtmusik
        # first-work case terminates at 'Play Work' (no wrapper).
        # Just dump the shape so a regression in the work-drill
        # behaviour surfaces here.


if __name__ == "__main__":
    unittest.main()
