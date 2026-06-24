"""Live test for session-splitting: concurrent sibling album drills.

Drilling many albums from one artist's album list at the same time must return
each album's own content — the failure this fixes returned a duplicated/wrong
album. We prove it by running a sequential ground-truth pass and a concurrent
pass and asserting the concurrent output reproduces the sequential output
exactly (per album, and for the resolved Queue gateway of each album's first
track).

Read-only: asserts at the ``get_media_actions`` seam (the Queue gateway is
reachable for the *right* track); it never dispatches an action.

Behaviour-only: it asserts on observable output (track titles, action titles,
the action-list title) — never on session keys or other internals.

Set ``ROON_TEST_ARTIST_A`` in ``agent/.env.test`` to a search whose library has
at least two albums (defaults to "Beatles"). The test searches it, drills the
"Albums" category (whose ``extra_info`` reads "X Results"), and uses those
albums — so it doesn't depend on the top result being the artist or on the
artist's albums being in the library. It skips with a warning if there's no
"Albums" category with >= 2 results.

Run with:
    ./dev pytest tests/test_session_split_live.py -v -m live_roon
"""

import asyncio
import logging
import os
import re

import pytest

from tools.roon_search import (
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
)

_log = logging.getLogger("swarpius.session_split_live")

pytestmark = pytest.mark.live_roon

ARTIST = os.environ.get("ROON_TEST_ARTIST_A", "Beatles")
_RESULTS_RE = re.compile(r"(\d+)\s+Results?", re.IGNORECASE)


def _is_gateway(item) -> bool:
    """A 'Play Artist' / 'Play Album' style navigation gateway, not content."""
    return item.title.startswith("Play ")


class TestConcurrentSiblingAlbumDrills:
    @classmethod
    def setup_class(cls):
        from tests.conftest import get_live_roon

        cls.roon = get_live_roon()
        cls.search_tool = RoonSearchTool(
            RoonSearchToolConfig(roon_connection=cls.roon),
        )

    def setup_method(self):
        if not self.roon.wait_for_connection(timeout=30):
            pytest.skip("Roon connection not available")

    # -- helpers ---------------------------------------------------------

    def _run(self, **kwargs):
        return asyncio.run(
            self.search_tool.run_async(RoonSearchToolInputSchema(**kwargs)),
        )

    def _album_items(self):
        """Search the term, drill the "Albums" category (>= 2 results required),
        and return its album items (gateways filtered, max 10)."""
        search = self._run(operation="new_search", search_string=ARTIST)
        items = [item for group in search.groups for item in group.items]

        albums_cat = next(
            (item for item in items if item.title.strip().lower() == "albums"),
            None,
        )
        if albums_cat is None:
            pytest.skip(f"No 'Albums' category in search results for {ARTIST!r}")
        match = _RESULTS_RE.search(albums_cat.extra_info or "")
        if not match or int(match.group(1)) < 2:
            pytest.skip(
                f"'Albums' category for {ARTIST!r} has < 2 results "
                f"(extra_info={albums_cat.extra_info!r}). Set ROON_TEST_ARTIST_A "
                f"to a search with at least two albums in your library.",
            )

        drill = self._run(operation="drill_down_reference", reference=albums_cat.reference)
        albums = [
            item
            for group in drill.groups
            for item in group.items
            if not _is_gateway(item)
        ]
        if len(albums) < 2:
            pytest.skip(
                f"Drilling 'Albums' for {ARTIST!r} returned < 2 albums, got {len(albums)}",
            )
        return albums[:10]

    def _fingerprint(self, drill_output):
        """Content-only fingerprint of an album drill: its sorted track titles,
        plus the resolved action context of its first track (proving which item
        the Queue gateway belongs to). Stable across sessions — no keys."""
        tracks = [
            item
            for group in drill_output.groups
            for item in group.items
            if not _is_gateway(item)
        ]
        track_titles = tuple(sorted(t.title for t in tracks))

        action_ctx = None
        if tracks:
            results, _sk, _levels = self.roon.get_media_actions(tracks[0])
            if results:
                action_titles = tuple(sorted(i.title for i in (results.items or [])))
                list_title = results.list.title if results.list else None
                action_ctx = (tracks[0].title, list_title, action_titles)
        return (track_titles, action_ctx)

    # -- test ------------------------------------------------------------

    def test_concurrent_sibling_drills_match_sequential_ground_truth(self):
        # Sequential ground truth — fresh refs, one drill at a time, no race.
        seq_albums = self._album_items()
        oracle = [
            self._fingerprint(
                self._run(operation="drill_down_reference", reference=a.reference),
            )
            for a in seq_albums
        ]

        # Sanity: the path actually reaches a Queue gateway, else the test
        # isn't exercising what we think (e.g. an odd library/artist).
        assert any(fp[1] and "Queue" in fp[1][2] for fp in oracle), (
            "No album's first track yielded a Queue action — check the artist/library"
        )

        # Concurrent run — fresh refs (all on one session ⇒ maximum contention),
        # drilled all at once the way the parallel tool loop does.
        con_albums = self._album_items()
        if [a.title for a in con_albums] != [a.title for a in seq_albums]:
            pytest.skip("Album list ordering changed between runs; cannot align")

        async def _drill_all():
            return await asyncio.gather(*[
                asyncio.to_thread(
                    asyncio.run,
                    self.search_tool.run_async(RoonSearchToolInputSchema(
                        operation="drill_down_reference", reference=a.reference,
                    )),
                )
                for a in con_albums
            ])

        concurrent_drills = asyncio.run(_drill_all())
        observed = [self._fingerprint(d) for d in concurrent_drills]

        for index, album in enumerate(seq_albums):
            assert observed[index] == oracle[index], (
                f"Concurrent drill of album #{index} {album.title!r} diverged "
                f"from its sequential ground truth — session cross-contamination"
            )
