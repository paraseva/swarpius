"""Unit tests for category reconciliation in :mod:`roon.browse`.

Covers the track→container correction path (``_correct_via_category_search``)
for both album and playlist intents. A hard miss must raise
``CategoryCorrectionFailed`` so the caller can surface a
coordinator-actionable retry hint — never substring-match a fallback
candidate (a "Voices" track ref intended as an album would otherwise
land on a karaoke compilation containing the word "voices").
"""

from __future__ import annotations

import unittest
from typing import List, Optional

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.exceptions import CategoryCorrectionFailed  # noqa: E402
from roon_core.browse import RoonBrowseMixin  # noqa: E402
from roon_core.browse_session import (  # noqa: E402
    ItemIdentity,
    SearchRecipe,
    StableReference,
)
from roon_core.schemas import (  # noqa: E402
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)


def _item(title: str, key: str, hint: Optional[str] = "list") -> RoonCoreItemSchema:
    return RoonCoreItemSchema(title=title, item_key=key, hint=hint)


def _results(
    items: List[RoonCoreItemSchema],
    list_title: Optional[str] = None,
    list_hint: Optional[str] = None,
) -> RoonCoreResultsSchema:
    return RoonCoreResultsSchema(
        items=items,
        list=RoonCoreListSchema(count=len(items), title=list_title, hint=list_hint),
    )


class _FakeSessionManager:
    @property
    def recovery_session_key(self) -> str:
        return "recovery"

    def update_ref_key(
        self,
        ref: StableReference,
        new_item_key: str,
        new_session_key: str,
        new_item_key_path: Optional[List[str]] = None,
    ) -> None:
        ref.cached_item_key = new_item_key
        ref.roon_session_key = new_session_key
        if new_item_key_path is not None:
            ref.item_key_path = list(new_item_key_path)


class _BrowseHost(RoonBrowseMixin):
    """Concrete RoonBrowseMixin-only instance for unit-testing the
    reconcile path without spinning up a Roon connection.

    ``browse_responses`` and ``drill_responses`` are scripted in order
    of expected calls — the corrector first ``browse_core``s a fresh
    search, then ``_nav_drill``s into the category, then ``_nav_drill``s
    into the matched item (when a match is found).
    """

    def __init__(
        self,
        browse_responses: List[RoonCoreResultsSchema],
        drill_responses: List[RoonCoreResultsSchema],
    ) -> None:
        self.session_manager = _FakeSessionManager()
        self._browse_queue = list(browse_responses)
        self._drill_queue = list(drill_responses)
        self.reset_calls: List[str] = []

    def browse_core(self, aux, zone=None, session_key=None, update_current=True):
        return self._browse_queue.pop(0)

    def _nav_drill(self, item_key, session_key, zone=None, update_current=True):
        return self._drill_queue.pop(0)

    def _nav_reset_to_root(self, session_key, zone=None):
        self.reset_calls.append(session_key)


def _make_track_ref(title: str = "Voices") -> StableReference:
    return StableReference(
        ref_id="c8a73",
        identity=ItemIdentity(title=title, hint="action_list"),
        recipe=SearchRecipe(search_string=f"{title} Russ Ballard"),
        cached_item_key="69:0",
        roon_session_key="s-test",
        item_key_path=[],
    )


def _track_wrapper_results() -> RoonCoreResultsSchema:
    """Single-child wrapper around an action-list child — what
    ``_nav_drill`` returns for a track-shaped reference."""
    return _results(
        [_item("Voices", "70:0", hint="action_list")],
        list_title="Voices",
        list_hint=None,
    )


class TestCategoryCorrectionAlbum(unittest.TestCase):
    """track→album reconciliation."""

    def test_strict_title_match_wins(self):
        """Exact normalised-title match in the Albums list drills cleanly."""
        ref = _make_track_ref()
        host = _BrowseHost(
            browse_responses=[
                _results(
                    [
                        _item("Voices", "71:0", hint="action_list"),
                        _item("Albums", "71:1"),
                    ],
                ),
            ],
            drill_responses=[
                # Albums category contents
                _results(
                    [
                        _item("Voices", "72:0"),
                        _item("Hope", "72:1"),
                    ],
                ),
                # Inside the matched "Voices" album
                _results(
                    [_item("Play Album", "73:0", hint="action_list")],
                    list_title="Voices",
                ),
            ],
        )

        result = host.reconcile_intended_category(
            ref, "album", _track_wrapper_results(), "s-test",
        )
        self.assertIsNotNone(result, "Strict match should produce a correction")
        results, sk, levels = result
        self.assertEqual(sk, "recovery")
        self.assertEqual(levels, 2)
        self.assertEqual([i.title for i in results.items], ["Play Album"])

    def test_no_match_raises(self):
        """No album with the right title — corrector raises rather than
        substring-matching an unrelated hit (the karaoke-compilation bug)."""
        ref = _make_track_ref()
        host = _BrowseHost(
            browse_responses=[
                _results([_item("Albums", "71:1")]),
            ],
            drill_responses=[
                # Albums list: nothing titled "Voices", but a substring
                # match exists ("Sing Voices Like Russ Ballard").
                # A pure substring match here would be a false positive;
                # the resolver must raise.
                _results(
                    [
                        _item(
                            "The Karaoke Channel - Sing Voices Like Russ Ballard",
                            "72:0",
                        ),
                        _item("Hope (feat. Nelson Mandela)", "72:1"),
                    ],
                ),
            ],
        )

        with self.assertRaises(CategoryCorrectionFailed) as cm:
            host.reconcile_intended_category(
                ref, "album", _track_wrapper_results(), "s-test",
            )
        self.assertEqual(cm.exception.intended_category, "album")
        self.assertEqual(cm.exception.category_name, "Albums")
        self.assertEqual(cm.exception.title, "Voices")
        self.assertEqual(cm.exception.ref_id, "c8a73")
        self.assertEqual(cm.exception.failure_mode, "no_match")
        # Corrector resets the recovery session before raising so it's
        # left in a clean state for the next request.
        self.assertEqual(host.reset_calls, ["recovery"])

    def test_no_category_raises(self):
        """The Albums category isn't even in the search results — the
        symmetric failure mode. browse_core(pop_all=True) already reset
        the session, so no _nav_reset_to_root call is needed before
        raising."""
        ref = _make_track_ref()
        host = _BrowseHost(
            browse_responses=[
                # No "Albums" item — only Tracks/Works/etc.
                _results(
                    [
                        _item("Voices", "71:0", hint="action_list"),
                        _item("Tracks", "71:2"),
                    ],
                ),
            ],
            drill_responses=[],
        )

        with self.assertRaises(CategoryCorrectionFailed) as cm:
            host.reconcile_intended_category(
                ref, "album", _track_wrapper_results(), "s-test",
            )
        self.assertEqual(cm.exception.failure_mode, "no_category")
        self.assertEqual(cm.exception.category_name, "Albums")
        # No drills happened, so no reset call expected.
        self.assertEqual(host.reset_calls, [])


class TestCategoryCorrectionPlaylist(unittest.TestCase):
    """track→playlist reconciliation — symmetric with album."""

    def test_no_match_raises_with_playlists_category(self):
        ref = _make_track_ref(title="My Mix")
        host = _BrowseHost(
            browse_responses=[
                _results([_item("Playlists", "71:1")]),
            ],
            drill_responses=[
                _results([_item("Some Other Playlist", "72:0")]),
            ],
        )
        with self.assertRaises(CategoryCorrectionFailed) as cm:
            host.reconcile_intended_category(
                ref, "playlist", _track_wrapper_results(), "s-test",
            )
        self.assertEqual(cm.exception.intended_category, "playlist")
        self.assertEqual(cm.exception.category_name, "Playlists")
        self.assertEqual(cm.exception.failure_mode, "no_match")

    def test_no_category_raises_for_playlist_intent(self):
        """Search returned no Playlists category (search 'Voices Russ
        Ballard' has no Playlists section because a playlist titled
        'Voices' lacks the 'Russ Ballard' token)."""
        ref = _make_track_ref(title="Voices")
        host = _BrowseHost(
            browse_responses=[
                _results(
                    [
                        _item("Voices", "71:0", hint="action_list"),
                        _item("Albums", "71:1"),
                        _item("Tracks", "71:2"),
                        _item("Works", "71:3"),
                    ],
                ),
            ],
            drill_responses=[],
        )
        with self.assertRaises(CategoryCorrectionFailed) as cm:
            host.reconcile_intended_category(
                ref, "playlist", _track_wrapper_results(), "s-test",
            )
        self.assertEqual(cm.exception.failure_mode, "no_category")
        self.assertEqual(cm.exception.category_name, "Playlists")


class TestCategoryCorrectionGatewaySibling(unittest.TestCase):
    """album->track reconciliation: a container resolved where a track was
    intended. When no sibling track matches the container's identity, the
    corrector fails loud -- the mirror of
    ``TestCategoryCorrectionAlbum.test_no_match_raises``."""

    def _album_ref(self, title: str) -> StableReference:
        return StableReference(
            ref_id="d9282",
            identity=ItemIdentity(title=title, hint="list"),
            recipe=SearchRecipe(search_string="It's Your Thing Isley Brothers"),
            cached_item_key="36:0",
            roon_session_key="s-test",
            item_key_path=[],
        )

    def test_no_sibling_match_raises(self):
        """Album resolved, track intended, and no track inside it shares
        the album's identity title -- fail loud rather than silently
        falling through to the album."""
        ref = self._album_ref("It's Your Thing: The Story Of The Isley Brothers")
        host = _BrowseHost(browse_responses=[], drill_responses=[])
        gateway_level = _results(
            [
                _item("Play Album", "36:0", hint="action_list"),
                _item("It's Your Thing (Album Version)", "36:11", hint="action_list"),
                _item("Twist & Shout (Album Version)", "36:4", hint="action_list"),
            ],
            list_hint=None,
        )

        with self.assertRaises(CategoryCorrectionFailed) as cm:
            host.reconcile_intended_category(ref, "track", gateway_level, "s-test")
        self.assertEqual(cm.exception.intended_category, "track")
        self.assertEqual(cm.exception.resolved_category, "album")
        self.assertEqual(cm.exception.failure_mode, "no_match")
        self.assertEqual(
            cm.exception.title,
            "It's Your Thing: The Story Of The Isley Brothers",
        )
        self.assertEqual(cm.exception.ref_id, "d9282")
        self.assertEqual(host.reset_calls, ["s-test"])


class TestCategoryCorrectionMessage(unittest.TestCase):
    """The message rendered by ``_format_category_correction_error``
    is what the coordinator sees verbatim — pin its shape for both
    failure modes."""

    def _exc(self, *, intended, category, mode, title="Voices", ref_id="c8a73"):
        return CategoryCorrectionFailed(
            ref_id=ref_id,
            title=title,
            intended_category=intended,
            category_name=category,
            failure_mode=mode,
        )

    def test_album_no_match_message(self):
        from roon_core.schemas import RoonCoreItemSummarySchema
        from tools.roon_action import _format_category_correction_error

        item = RoonCoreItemSummarySchema(title="Voices", reference="S:c8a73")
        msg = _format_category_correction_error(
            item,
            self._exc(intended="album", category="Albums", mode="no_match"),
        )
        self.assertIn("Item 'Voices' (ref S:c8a73) resolved as a track", msg)
        self.assertIn(
            "no album titled 'Voices' was found in the Albums category", msg,
        )
        self.assertIn("intended_item_category='track'", msg)
        self.assertIn(
            "re-search and pick from the Albums category directly", msg,
        )

    def test_playlist_no_match_message(self):
        from roon_core.schemas import RoonCoreItemSummarySchema
        from tools.roon_action import _format_category_correction_error

        item = RoonCoreItemSummarySchema(title="My Mix", reference="S:abc12")
        msg = _format_category_correction_error(
            item,
            self._exc(
                intended="playlist", category="Playlists", mode="no_match",
                title="My Mix", ref_id="abc12",
            ),
        )
        self.assertIn(
            "no playlist titled 'My Mix' was found in the Playlists category",
            msg,
        )
        self.assertIn(
            "re-search and pick from the Playlists category directly", msg,
        )

    def test_no_category_message_omits_drill_hint(self):
        """The no_category branch must not suggest drilling into the
        category — it isn't there to drill."""
        from roon_core.schemas import RoonCoreItemSummarySchema
        from tools.roon_action import _format_category_correction_error

        item = RoonCoreItemSummarySchema(title="Voices", reference="S:c8a73")
        msg = _format_category_correction_error(
            item,
            self._exc(intended="playlist", category="Playlists", mode="no_category"),
        )
        self.assertIn("resolved as a track", msg)
        self.assertIn(
            "search returned no matching playlists "
            "(no Playlists category in the results)",
            msg,
        )
        self.assertIn("intended_item_category='track'", msg)
        self.assertIn(
            "re-search with terms that would surface the playlist", msg,
        )
        # Critically: the drill-into-category language must be absent.
        self.assertNotIn("pick from the Playlists category directly", msg)
        self.assertNotIn("Playlists category directly", msg)

    def test_gateway_sibling_message_names_resolved_and_intended(self):
        """album->track miss: the directive states the actual resolved
        category (album), offers relabelling to it, and points at the
        intended category to pick from."""
        from roon_core.schemas import RoonCoreItemSummarySchema
        from tools.roon_action import _format_category_correction_error

        item = RoonCoreItemSummarySchema(
            title="It's Your Thing", reference="S:d9282",
        )
        exc = CategoryCorrectionFailed(
            ref_id="d9282",
            title="It's Your Thing: The Story Of The Isley Brothers",
            intended_category="track",
            category_name="Tracks",
            failure_mode="no_match",
            resolved_category="album",
        )
        msg = _format_category_correction_error(item, exc)
        self.assertIn("resolved as an album, not a track", msg)
        self.assertIn("intended_item_category='album'", msg)
        self.assertIn("Tracks category", msg)

if __name__ == "__main__":
    unittest.main()
