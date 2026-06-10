"""Live test for the StopMarkerCoordinator.

Read-only — drives ``StopMarkerCoordinator.initialise()`` against the
running Roon Core and asserts the coordinator caches a
``track_item_key`` whose drill *still* yields a ``Play Now`` action
on a second call. That second drill is what makes cached re-use
necessary: every steady-state stop dispatch starts by drilling
this exact key on the same session, expecting the action_list back.

Tests the *code path*, not just file presence — when the marker isn't
installed, the test skips with setup instructions; otherwise it
exercises ``initialise`` and the cached-key drill stability that the
dispatch path relies on.

Skips silently if the marker isn't found, so this test is safe to
run whether or not the user has installed the file yet.

Run with:
    ./dev pytest tests/test_stop_marker_live.py -v -m live_roon
"""

import logging
import unittest

import pytest

from app.roon.stop_marker import StopMarkerCoordinator
from app.settings import get_settings

_log = logging.getLogger("swarpius.stop_marker_live")

pytestmark = pytest.mark.live_roon


class TestStopMarkerCoordinatorReachable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.conftest import get_live_roon
        cls.roon = get_live_roon()

    def setUp(self):
        if not self.roon.wait_for_connection(timeout=30):
            self.skipTest("Roon connection not available")

    def test_initialise_caches_stable_track_item_key(self):
        marker_title = get_settings().stop_marker_title
        _log.info("Looking up stop marker: %r", marker_title)

        coord = StopMarkerCoordinator(
            connection=self.roon,
            marker_title=marker_title,
            broadcast_state=lambda: None,
        )
        ok = coord.initialise()
        if not ok:
            self.skipTest(
                f"{marker_title!r} not surfaced — install the silent "
                "marker file and let Roon scan it (see README).",
            )

        self.assertTrue(coord.available)
        self.assertIsNotNone(coord.track_item_key)
        track_key = coord.track_item_key
        _log.info(
            "Stop marker initialised: track_item_key=%s (post-wrapper, if any)",
            track_key,
        )

        # Drill #1: the cached key must yield an action_list with
        # Play Now. This is the first browse_core every dispatch_stop
        # makes — if it fails, the dispatch path can't proceed.
        sk = self.roon.session_manager.stop_session_key
        action_list_1 = self.roon.browse_core(
            aux={"item_key": track_key},
            session_key=sk,
            update_current=False,
        )
        play_now_1 = self.roon.find_item_by_field(
            items=action_list_1.items or [],
            field_name="title",
            field_value="Play Now",
        )
        self.assertIsNotNone(
            play_now_1,
            f"Drill of cached track_item_key returned no Play Now "
            f"action. Items: {[i.title for i in (action_list_1.items or [])]}",
        )

        # Drill #2: same cached key, same session, no pop in between.
        # Validates the critical assumption that the track-level
        # item_key is stable across calls — exactly what enables every
        # subsequent stop to reuse the cache without re-searching.
        action_list_2 = self.roon.browse_core(
            aux={"item_key": track_key},
            session_key=sk,
            update_current=False,
        )
        play_now_2 = self.roon.find_item_by_field(
            items=action_list_2.items or [],
            field_name="title",
            field_value="Play Now",
        )
        self.assertIsNotNone(
            play_now_2,
            "Second drill of the cached track_item_key did not return "
            "Play Now — the cached item_key is not stable across "
            "calls, so the coordinator's steady-state optimisation "
            "won't hold.",
        )


if __name__ == "__main__":
    unittest.main()
