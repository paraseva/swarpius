"""Tests for StopMarkerCoordinator.

The coordinator owns cached state for the silent-marker stop feature
and reserves a dedicated browse session so steady-state stops cost
exactly two browse calls (drill cached track item → dispatch Play Now)
rather than the search + drill chain the original ``perform_stop`` ran
on every invocation.

The connection fake stubs only the Roon-API boundary (``browse_core``,
``set_auto_radio``, ``find_item_by_field``); ``stop_session_key``
allocation, ``new_search_session`` avoiding that key, and depth tracking
are checked.

Coverage:

* ``initialise()`` caches ``track_item_key`` and flips ``available``
  when the marker is present
* ``initialise()`` strips an N=1 same-titled wrapper and caches the
  *post*-wrapper item_key (so steady-state stops only do 2 browse calls)
* ``initialise()`` returns False and clears state when marker absent
* Steady-state ``dispatch_stop`` does exactly drill+drill (no search)
* Stale cached key recovers via single re-init + single retry
* Persistent unavailability returns ``use_pause_fallback`` with zero
  dispatches
* Re-init succeeds but the new dispatch still fails → terminal error,
  no third attempt, no auto-radio change
* ``set_auto_radio(False)`` fires only on terminal success — never on
  failure paths
* Custom marker title via ``ROON_STOP_MARKER_TITLE`` honoured
* Disabled mode (``DISABLE_SIMULATED_STOP``) → never inits, every
  ``dispatch_stop`` is a pause-fallback
* ``available`` flips broadcast ``feature_availability`` exactly once
  per state change (including the dispatch-time recovery path)
"""

import unittest
from typing import List, Optional

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.roon.stop_marker import StopMarkerCoordinator, StopResult  # noqa: E402
from app.settings import get_settings  # noqa: E402
from roon_core.browse_session import BrowseSessionManager  # noqa: E402

# Source the marker from settings so a change to ROON_STOP_MARKER_TITLE
# (or its default in app.settings.core) propagates here automatically.
STOP_MARKER_TITLE = get_settings().stop_marker_title
from roon_core.schemas import (  # noqa: E402
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)

# ─────────────────────────────────────────────────────────────────────
# Fake connection
# ─────────────────────────────────────────────────────────────────────


class _ConnectionFake:
    """Connection fake exposing only the API boundary the coordinator
    needs. Owns a real ``BrowseSessionManager`` so the production
    ``stop_session_key`` reservation and ref bookkeeping all run live.

    Configurable per-test via ``set_marker_present`` (toggle whether the
    search returns the marker), ``wrap_marker`` (insert the N=1
    same-titled wrapper between the marker and its action_list), and
    ``fail_track_drill_once`` / ``fail_track_drill_always`` to simulate
    a stale cached track_item_key.
    """

    MARKER_KEY = "131:0"
    WRAPPER_KEY = "132:0"
    PLAY_NOW_KEY = "200:0"

    def __init__(
        self,
        marker_title: str = STOP_MARKER_TITLE,
        marker_present: bool = True,
        wrap_marker: bool = False,
    ) -> None:
        self.session_manager = BrowseSessionManager()
        self.marker_title = marker_title
        self.marker_present = marker_present
        self.wrap_marker = wrap_marker

        # Drill-stale toggles. ``fail_track_drill_once`` fires on the
        # next track-drill only and then auto-resets — models a single
        # transient stale-key failure that recovery should heal. The
        # ``always`` variant models a track_item_key that won't recover
        # on retry either (e.g. dispatch primitive itself broken).
        self.fail_track_drill_once = False
        self.fail_track_drill_always = False
        self.fail_play_now_drill_always = False

        # Recorders (positional log of every browse_core call).
        self.browse_aux_calls: List[dict] = []
        self.browse_zones: List[Optional[str]] = []
        self.browse_session_keys: List[Optional[str]] = []
        self.set_auto_radio_calls: List[dict] = []

    # ── API boundary ─────────────────────────────────────────────

    def browse_core(
        self,
        aux: dict,
        zone: Optional[str] = None,
        session_key: Optional[str] = None,
        update_current: bool = True,
    ) -> RoonCoreResultsSchema:
        _ = update_current
        self.browse_aux_calls.append(dict(aux))
        self.browse_zones.append(zone)
        self.browse_session_keys.append(session_key)

        if "input" in aux and aux.get("pop_all"):
            return self._search_response()

        item_key = aux.get("item_key")
        if item_key == self.MARKER_KEY:
            return self._marker_drill_response()
        if item_key == self.WRAPPER_KEY:
            return self._action_list_response()
        if item_key == self.PLAY_NOW_KEY:
            if self.fail_play_now_drill_always:
                raise RuntimeError("simulated Play Now dispatch failure")
            return self._empty_response()

        # Unknown drill — simulates a stale cached key.
        raise RuntimeError(
            f"simulated stale-key dispatch failure for item_key={item_key}"
        )

    def find_item_by_field(
        self,
        items: List[RoonCoreItemSchema],
        field_name: str,
        field_value: str,
    ) -> Optional[RoonCoreItemSchema]:
        for it in items:
            value = getattr(it, field_name, None)
            if isinstance(value, str) and value.lower() == field_value.lower():
                return it
        return None

    def set_auto_radio(
        self, auto_radio: bool, zone: Optional[str] = None,
    ) -> None:
        self.set_auto_radio_calls.append(
            {"auto_radio": auto_radio, "zone": zone},
        )

    # ── Programmable response shapes ─────────────────────────────

    def _search_response(self) -> RoonCoreResultsSchema:
        if self.marker_present:
            items = [
                RoonCoreItemSchema(
                    title=self.marker_title,
                    item_key=self.MARKER_KEY,
                    hint="action_list",
                    subtitle="Local",
                ),
            ]
        else:
            items = [
                RoonCoreItemSchema(
                    title="Some Other Track",
                    item_key="other:0",
                    hint="list",
                ),
            ]
        return RoonCoreResultsSchema(
            items=items,
            list=RoonCoreListSchema(count=len(items), hint=None, title=""),
        )

    def _marker_drill_response(self) -> RoonCoreResultsSchema:
        # Stale-cached-key simulation: the cached track_item_key here is
        # MARKER_KEY (no wrapper) or WRAPPER_KEY (wrapper). The "fail
        # once" flag clears itself after firing.
        if self.fail_track_drill_always:
            raise RuntimeError("simulated persistent track-drill failure")
        if self.fail_track_drill_once:
            self.fail_track_drill_once = False
            raise RuntimeError("simulated transient stale-key failure")

        if self.wrap_marker:
            return RoonCoreResultsSchema(
                items=[
                    RoonCoreItemSchema(
                        title=self.marker_title,
                        item_key=self.WRAPPER_KEY,
                        hint="action_list",
                    ),
                ],
                list=RoonCoreListSchema(
                    count=1, hint=None, title=self.marker_title,
                ),
            )
        return self._action_list_response()

    def _action_list_response(self) -> RoonCoreResultsSchema:
        return RoonCoreResultsSchema(
            items=[
                RoonCoreItemSchema(
                    title="Play Now",
                    item_key=self.PLAY_NOW_KEY,
                    hint="Action",
                ),
                RoonCoreItemSchema(
                    title="Add Next",
                    item_key="addnext:0",
                    hint="Action",
                ),
                RoonCoreItemSchema(
                    title="Queue",
                    item_key="queue:0",
                    hint="Action",
                ),
            ],
            list=RoonCoreListSchema(
                count=3, hint="action_list", title=self.marker_title,
            ),
        )

    def _empty_response(self) -> RoonCoreResultsSchema:
        return RoonCoreResultsSchema(
            items=[], list=RoonCoreListSchema(count=0, hint=None, title=""),
        )


def _make_coordinator(
    conn: _ConnectionFake,
    marker_title: str = STOP_MARKER_TITLE,
    disabled: bool = False,
) -> tuple[StopMarkerCoordinator, List[None]]:
    """Build a real coordinator + a list that records each broadcast."""
    broadcasts: List[None] = []
    coord = StopMarkerCoordinator(
        connection=conn,
        marker_title=marker_title,
        broadcast_state=lambda: broadcasts.append(None),
        disabled=disabled,
    )
    return coord, broadcasts


def _drill_keys(conn: _ConnectionFake) -> List[str]:
    """All item_keys passed to browse_core, in order."""
    return [aux.get("item_key") for aux in conn.browse_aux_calls
            if "item_key" in aux]


def _search_calls(conn: _ConnectionFake) -> List[dict]:
    """All search calls (pop_all + input) passed to browse_core."""
    return [aux for aux in conn.browse_aux_calls if aux.get("pop_all")]


# ─────────────────────────────────────────────────────────────────────
# initialise()
# ─────────────────────────────────────────────────────────────────────


class TestInitialiseCachesState(unittest.TestCase):
    def test_initialise_caches_marker_key_and_flips_available(self):
        conn = _ConnectionFake()
        coord, broadcasts = _make_coordinator(conn)
        self.assertFalse(coord.available)

        ok = coord.initialise()

        self.assertTrue(ok)
        self.assertTrue(coord.available)
        self.assertEqual(coord.track_item_key, _ConnectionFake.MARKER_KEY)
        # State flipped (False → True) → exactly one broadcast.
        self.assertEqual(len(broadcasts), 1)
        # Calls hit the dedicated stop session key.
        sk = conn.session_manager.stop_session_key
        self.assertTrue(all(s == sk for s in conn.browse_session_keys))

    def test_initialise_strips_n1_wrapper_and_caches_wrapper_key(self):
        conn = _ConnectionFake(wrap_marker=True)
        coord, _ = _make_coordinator(conn)

        ok = coord.initialise()

        self.assertTrue(ok)
        # The cached key must be the wrapper's — that's what yields the
        # action_list directly. Caching the marker key would force a
        # 3-browse stop dispatch every time.
        self.assertEqual(coord.track_item_key, _ConnectionFake.WRAPPER_KEY)
        # Walk: search, drill marker, drill wrapper. No Play Now drill.
        keys = _drill_keys(conn)
        self.assertEqual(
            keys, [_ConnectionFake.MARKER_KEY, _ConnectionFake.WRAPPER_KEY],
        )

    def test_initialise_marker_absent_clears_state(self):
        conn = _ConnectionFake(marker_present=False)
        coord, broadcasts = _make_coordinator(conn)

        ok = coord.initialise()

        self.assertFalse(ok)
        self.assertFalse(coord.available)
        self.assertIsNone(coord.track_item_key)
        # State did not change (False → False) → no broadcast.
        self.assertEqual(broadcasts, [])

    def test_re_initialise_after_marker_disappears_broadcasts_once(self):
        conn = _ConnectionFake(marker_present=True)
        coord, broadcasts = _make_coordinator(conn)

        coord.initialise()           # False → True, broadcast #1
        self.assertEqual(len(broadcasts), 1)

        conn.marker_present = False
        coord.initialise()           # True → False, broadcast #2
        self.assertEqual(len(broadcasts), 2)
        self.assertFalse(coord.available)

        coord.initialise()           # False → False, no broadcast
        self.assertEqual(len(broadcasts), 2)


# ─────────────────────────────────────────────────────────────────────
# dispatch_stop — happy path
# ─────────────────────────────────────────────────────────────────────


class TestDispatchStopSteadyState(unittest.TestCase):
    def test_steady_state_stop_does_two_browse_calls_no_search(self):
        conn = _ConnectionFake()
        coord, _ = _make_coordinator(conn)
        coord.initialise()
        # Reset recorders so we can observe only the dispatch path.
        conn.browse_aux_calls.clear()
        conn.browse_zones.clear()
        conn.browse_session_keys.clear()
        conn.set_auto_radio_calls.clear()

        result = coord.dispatch_stop(zone="Living Room")

        self.assertEqual(
            result, StopResult(succeeded=True, use_pause_fallback=False),
        )
        # Two drills total: cached track key, then Play Now.
        self.assertEqual(
            _drill_keys(conn),
            [_ConnectionFake.MARKER_KEY, _ConnectionFake.PLAY_NOW_KEY],
        )
        self.assertEqual(_search_calls(conn), [])
        # All on the stop session, all with the dispatch zone.
        sk = conn.session_manager.stop_session_key
        self.assertTrue(all(s == sk for s in conn.browse_session_keys))
        self.assertTrue(all(z == "Living Room" for z in conn.browse_zones))

    def test_set_auto_radio_fires_only_after_successful_dispatch(self):
        conn = _ConnectionFake()
        coord, _ = _make_coordinator(conn)
        coord.initialise()
        conn.set_auto_radio_calls.clear()

        coord.dispatch_stop(zone="Living Room")

        # Exactly one call, after dispatch (recorded in test order).
        self.assertEqual(len(conn.set_auto_radio_calls), 1)
        self.assertEqual(
            conn.set_auto_radio_calls[0],
            {"auto_radio": False, "zone": "Living Room"},
        )

    def test_steady_state_with_wrapper_caches_post_wrapper_key(self):
        # End-to-end: wrapper present, init walks through it, dispatch
        # uses the wrapper's key (one drill from the cached key reaches
        # action_list).
        conn = _ConnectionFake(wrap_marker=True)
        coord, _ = _make_coordinator(conn)
        coord.initialise()
        conn.browse_aux_calls.clear()
        conn.browse_session_keys.clear()

        result = coord.dispatch_stop(zone=None)

        self.assertTrue(result.succeeded)
        # Dispatch path: drill wrapper (= cached track key), then Play Now.
        self.assertEqual(
            _drill_keys(conn),
            [_ConnectionFake.WRAPPER_KEY, _ConnectionFake.PLAY_NOW_KEY],
        )


# ─────────────────────────────────────────────────────────────────────
# dispatch_stop — recovery path
# ─────────────────────────────────────────────────────────────────────


class TestDispatchStopRecovery(unittest.TestCase):
    def test_stale_cached_key_recovers_via_single_reinit(self):
        conn = _ConnectionFake()
        coord, broadcasts = _make_coordinator(conn)
        coord.initialise()
        broadcasts.clear()
        conn.browse_aux_calls.clear()
        conn.set_auto_radio_calls.clear()

        # Next track-drill fails; subsequent ones succeed (models a
        # transient stale-key error that re-init heals).
        conn.fail_track_drill_once = True

        result = coord.dispatch_stop(zone="Kitchen")

        self.assertTrue(
            result.succeeded,
            f"Expected recovery to succeed, got {result}",
        )
        # Sequence: failed track drill → re-init (search + drill) →
        # successful track drill → Play Now drill.
        keys = _drill_keys(conn)
        self.assertIn(_ConnectionFake.PLAY_NOW_KEY, keys)
        # Exactly one search call during the re-init.
        self.assertEqual(len(_search_calls(conn)), 1)
        # No broadcast — the available flag never flipped (True → True).
        self.assertEqual(broadcasts, [])
        # Auto-radio fires once, only after the successful retry.
        self.assertEqual(len(conn.set_auto_radio_calls), 1)

    def test_persistent_unavailability_returns_pause_fallback(self):
        # Marker absent from the start. dispatch_stop should attempt
        # initialise once, find nothing, return use_pause_fallback.
        # No browse dispatches against the (absent) marker.
        conn = _ConnectionFake(marker_present=False)
        coord, _ = _make_coordinator(conn)

        result = coord.dispatch_stop(zone="Bedroom")

        self.assertEqual(
            result, StopResult(succeeded=False, use_pause_fallback=True),
        )
        # Exactly one search (the init attempt). No drills against the
        # marker key (it never appeared in search results).
        self.assertEqual(len(_search_calls(conn)), 1)
        self.assertNotIn(_ConnectionFake.MARKER_KEY, _drill_keys(conn))
        self.assertNotIn(_ConnectionFake.PLAY_NOW_KEY, _drill_keys(conn))
        # No auto-radio change on the pause-fallback path.
        self.assertEqual(conn.set_auto_radio_calls, [])

    def test_marker_disappears_mid_session_falls_back_to_pause(self):
        # Initial init succeeds, then the file is removed. Next stop:
        # cached drill fails → re-init now reports absent → pause.
        conn = _ConnectionFake()
        coord, broadcasts = _make_coordinator(conn)
        coord.initialise()
        broadcasts.clear()

        conn.marker_present = False
        # Force the cached drill to fail, so the recovery path runs.
        conn.fail_track_drill_always = True

        result = coord.dispatch_stop(zone=None)

        self.assertEqual(
            result, StopResult(succeeded=False, use_pause_fallback=True),
        )
        # available flipped True → False → exactly one broadcast.
        self.assertEqual(len(broadcasts), 1)
        self.assertFalse(coord.available)
        self.assertEqual(conn.set_auto_radio_calls, [])

    def test_reinit_succeeds_but_retry_dispatch_still_fails(self):
        # Re-init walks marker → action_list fine (Play Now found, key
        # cached), but the Play Now dispatch primitive itself is broken
        # — terminal error, no third attempt, no auto-radio change.
        conn = _ConnectionFake()
        coord, _ = _make_coordinator(conn)
        coord.initialise()
        conn.browse_aux_calls.clear()
        conn.set_auto_radio_calls.clear()

        conn.fail_track_drill_once = True
        conn.fail_play_now_drill_always = True

        result = coord.dispatch_stop(zone=None)

        self.assertFalse(result.succeeded)
        self.assertFalse(result.use_pause_fallback)
        self.assertIsNotNone(result.error)
        # Only one re-init search; we never go round again.
        self.assertEqual(len(_search_calls(conn)), 1)
        self.assertEqual(conn.set_auto_radio_calls, [])


# ─────────────────────────────────────────────────────────────────────
# Disabled mode
# ─────────────────────────────────────────────────────────────────────


class TestDisabledMode(unittest.TestCase):
    def test_disabled_initialise_is_noop(self):
        conn = _ConnectionFake()
        coord, broadcasts = _make_coordinator(conn, disabled=True)

        ok = coord.initialise()

        self.assertFalse(ok)
        self.assertFalse(coord.available)
        self.assertEqual(conn.browse_aux_calls, [])
        self.assertEqual(broadcasts, [])

    def test_disabled_dispatch_always_returns_pause_fallback(self):
        conn = _ConnectionFake()
        coord, _ = _make_coordinator(conn, disabled=True)

        result = coord.dispatch_stop(zone="Living Room")

        self.assertEqual(
            result, StopResult(succeeded=False, use_pause_fallback=True),
        )
        # Disabled path is purely a sentinel — no Roon API calls at all.
        self.assertEqual(conn.browse_aux_calls, [])
        self.assertEqual(conn.set_auto_radio_calls, [])


# ─────────────────────────────────────────────────────────────────────
# Custom marker title
# ─────────────────────────────────────────────────────────────────────


class TestCustomMarkerTitle(unittest.TestCase):
    def test_custom_title_used_in_search(self):
        conn = _ConnectionFake(marker_title="My Custom Marker")
        coord, _ = _make_coordinator(conn, marker_title="My Custom Marker")

        ok = coord.initialise()

        self.assertTrue(ok)
        searches = _search_calls(conn)
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]["input"], "My Custom Marker")


# ─────────────────────────────────────────────────────────────────────
# Stop session reservation
# ─────────────────────────────────────────────────────────────────────


class TestStopSessionReservation(unittest.TestCase):
    def test_stop_session_key_is_stable_outside_round_robin_pool(self):
        # The 16-slot pool produces keys of the form ``s-{prefix}-{slot}``
        # while the stop session uses ``stop-{prefix}``. Even after
        # cycling through the entire pool, the stop key should never
        # collide and should remain available for the coordinator.
        sm = BrowseSessionManager(max_sessions=16)
        stop_key = sm.stop_session_key
        produced = {sm.new_search_session() for _ in range(64)}

        self.assertNotIn(stop_key, produced)
        # Stop key remains tracked by the session manager (so refs minted
        # against it stay live).
        self.assertIn(stop_key, sm._session_depth)


if __name__ == "__main__":
    unittest.main()
