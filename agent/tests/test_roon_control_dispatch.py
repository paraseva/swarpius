"""Tests for RuntimeState.execute_roon_control dispatch — covers
every branch plus the two delegated helpers (``_handle_list_zones``,
``_handle_set_default_zone``).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.exceptions import (
    RoonConnectionUnavailableError,
    UnsupportedActionError,
    ZoneLookupError,
)
from app.roon.control_service import RoonControlService
from app.runtime.state import RuntimeState

_td_keepalive: list = []


def _bare_runtime(
    with_connection: bool = True,
    stop_coordinator: object = None,
) -> RuntimeState:
    """Minimal RuntimeState shell for dispatch tests.
    ``resolve_zone_name`` returns the input unchanged by default so
    tests can assert on the exact zone passed to the connection."""
    from tests._runtime_fixtures import wire_zone_domain
    rs = object.__new__(RuntimeState)
    rs.roon_connection = MagicMock() if with_connection else None
    rs.stop_marker_coordinator = stop_coordinator
    rs._ws_send_callback = lambda _c, _p: None
    td = wire_zone_domain(rs)
    if td is not None:
        _td_keepalive.append(td)

    # Return zone name unchanged — dispatch tests focus on call shape,
    # not on zone-resolution logic (which has its own suite).
    rs.resolve_zone_name = lambda z: z  # type: ignore[method-assign]
    rs._get_alias_for_zone = lambda z: None  # type: ignore[method-assign]
    rs._broadcast_default_zone = lambda: None  # type: ignore[method-assign]

    rs.roon_control = RoonControlService(
        roon_connection_getter=lambda: rs.roon_connection,
        resolve_zone_name=lambda z: rs.resolve_zone_name(z),
        get_alias_for_zone=lambda z: rs._get_alias_for_zone(z),
        broadcast_default_zone=lambda: rs._broadcast_default_zone(),
        stop_marker_coordinator_getter=lambda: rs.stop_marker_coordinator,
    )
    return rs


# ------------------------------------------------------------------ #
#  execute_roon_control — top-level dispatch                          #
# ------------------------------------------------------------------ #

class TestExecuteRoonControlDispatch(unittest.TestCase):
    def test_missing_connection_raises(self):
        rs = _bare_runtime(with_connection=False)
        with self.assertRaises(RoonConnectionUnavailableError):
            rs.execute_roon_control({"action": "play"})

    def test_missing_action_raises(self):
        rs = _bare_runtime()
        with self.assertRaises(ValueError):
            rs.execute_roon_control({"action": ""})

    def test_unknown_action_raises(self):
        rs = _bare_runtime()
        with self.assertRaises(UnsupportedActionError):
            rs.execute_roon_control({"action": "no_such_thing"})

    def test_play_routes_to_playback_control(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({"action": "play", "zone": "Kitchen"})
        rs.roon_connection.playback_control.assert_called_once_with(
            control="play", zone="Kitchen",
        )
        self.assertEqual(result, {"ok": True, "action": "play", "zone": "Kitchen"})

    def test_transport_actions_all_route(self):
        # `stop` is intentionally excluded — it routes through the
        # StopMarkerCoordinator rather than playback_control. See the
        # ``test_stop_*`` cases below.
        for action in ("pause", "next", "previous"):
            with self.subTest(action=action):
                rs = _bare_runtime()
                rs.execute_roon_control({"action": action, "zone": "Kitchen"})
                rs.roon_connection.playback_control.assert_called_once_with(
                    control=action, zone="Kitchen",
                )

    def test_stop_dispatches_via_coordinator(self):
        """`stop` does not call playback_control directly — Roon's
        native stop is just pause. Instead it routes through the
        StopMarkerCoordinator's dispatch_stop. On a successful real
        stop, no pause is issued."""
        from app.roon.stop_marker import StopResult
        coord = MagicMock()
        coord.dispatch_stop.return_value = StopResult(
            succeeded=True, use_pause_fallback=False,
        )
        rs = _bare_runtime(stop_coordinator=coord)

        result = rs.execute_roon_control({"action": "stop", "zone": "Kitchen"})

        coord.dispatch_stop.assert_called_once_with(zone="Kitchen")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["action"], "stop")
        rs.roon_connection.playback_control.assert_not_called()

    def test_stop_falls_back_to_pause_when_marker_unavailable(self):
        """When the coordinator signals use_pause_fallback (disabled
        mode or marker not in library), the WS stop button silently
        degrades to pause — no banner, action reports success."""
        from app.roon.stop_marker import StopResult
        coord = MagicMock()
        coord.dispatch_stop.return_value = StopResult(
            succeeded=False, use_pause_fallback=True,
        )
        rs = _bare_runtime(stop_coordinator=coord)

        result = rs.execute_roon_control({"action": "stop", "zone": "Kitchen"})

        coord.dispatch_stop.assert_called_once_with(zone="Kitchen")
        rs.roon_connection.playback_control.assert_called_once_with(
            control="pause", zone="Kitchen",
        )
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["action"], "stop")

    def test_stop_returns_terminal_error_when_dispatch_fails(self):
        """When the coordinator fails terminally (succeeded=False,
        use_pause_fallback=False), the response carries the error
        for the chat-panel banner. No pause, no silent success."""
        from app.roon.stop_marker import StopResult
        coord = MagicMock()
        coord.dispatch_stop.return_value = StopResult(
            succeeded=False, use_pause_fallback=False,
            error="dispatch failed after re-init",
        )
        rs = _bare_runtime(stop_coordinator=coord)

        result = rs.execute_roon_control({"action": "stop", "zone": "Kitchen"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "dispatch failed after re-init")
        rs.roon_connection.playback_control.assert_not_called()

    def test_set_volume_requires_output(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({"action": "set_volume", "volume": 50})
        self.assertFalse(result["ok"])
        self.assertIn("output", result["error"])

    def test_set_volume_happy_path(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({
            "action": "set_volume", "output": "Out1", "volume": 60,
        })
        rs.roon_connection.set_volume_absolute.assert_called_once_with(
            volume=60, output="Out1",
        )
        rs.roon_connection.set_volume_percent.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["volume"], 60)

    def test_mute_requires_output(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({"action": "mute"})
        self.assertFalse(result["ok"])
        self.assertIn("output", result["error"])

    def test_mute_sends_bool(self):
        rs = _bare_runtime()
        rs.execute_roon_control({"action": "mute", "output": "Out1", "mute": True})
        rs.roon_connection.mute.assert_called_once_with(mute=True, output="Out1")

    def test_unmute_via_mute_false(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({
            "action": "mute", "output": "Out1", "mute": False,
        })
        rs.roon_connection.mute.assert_called_once_with(mute=False, output="Out1")
        self.assertFalse(result["mute"])

    def test_play_from_here_requires_queue_item_id(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({"action": "play_from_here"})
        self.assertFalse(result["ok"])
        self.assertIn("queue_item_id", result["error"])

    def test_play_from_here_happy_path(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({
            "action": "play_from_here", "queue_item_id": 42, "zone": "Kitchen",
        })
        rs.roon_connection.play_from_here.assert_called_once_with(
            queue_item_id=42, zone="Kitchen",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["queue_item_id"], 42)

    def test_seek_requires_position(self):
        rs = _bare_runtime()
        with self.assertRaises(ValueError):
            rs.execute_roon_control({"action": "seek", "zone": "Kitchen"})

    def test_seek_rejects_negative_position(self):
        rs = _bare_runtime()
        with self.assertRaises(ValueError):
            rs.execute_roon_control({
                "action": "seek", "zone": "Kitchen", "position_seconds": -5,
            })

    def test_seek_happy_path(self):
        rs = _bare_runtime()
        result = rs.execute_roon_control({
            "action": "seek", "zone": "Kitchen", "position_seconds": 120,
        })
        rs.roon_connection.seek.assert_called_once_with(
            seconds=120, method="absolute", zone="Kitchen",
        )
        self.assertEqual(result["position_seconds"], 120)

    def test_zone_input_is_resolved(self):
        """resolve_zone_name should be called for non-set_volume / non-mute
        actions that take a zone. Here we swap in a spy so we can see it."""
        rs = _bare_runtime()
        resolve_spy = MagicMock(side_effect=lambda z: f"resolved-{z}")
        rs.resolve_zone_name = resolve_spy  # type: ignore[method-assign]
        rs.execute_roon_control({"action": "play", "zone": "kitchen"})
        resolve_spy.assert_called_once_with("kitchen")
        rs.roon_connection.playback_control.assert_called_once_with(
            control="play", zone="resolved-kitchen",
        )


# ------------------------------------------------------------------ #
#  _handle_list_zones                                                 #
# ------------------------------------------------------------------ #

class TestHandleListZones(unittest.TestCase):
    def _rs_with_zones(self, zones_info, default_zone="Kitchen"):
        rs = _bare_runtime()
        rs.roon_connection.get_zones_with_group_info.return_value = zones_info
        rs.roon_connection.get_default_zone.return_value = default_zone
        return rs

    def test_sort_default_first(self):
        rs = self._rs_with_zones([
            {"display_name": "Study", "state": "playing",
             "is_grouped": False, "group_members": []},
            {"display_name": "Kitchen", "state": "stopped",
             "is_grouped": False, "group_members": []},
        ], default_zone="Kitchen")
        result = rs._handle_list_zones()
        self.assertEqual(result["zones"][0]["display_name"], "Kitchen")
        self.assertTrue(result["zones"][0]["is_default"])

    def test_sort_by_state_then_name(self):
        rs = self._rs_with_zones([
            {"display_name": "Study",    "state": "stopped",
             "is_grouped": False, "group_members": []},
            {"display_name": "Kitchen",  "state": "playing",
             "is_grouped": False, "group_members": []},
            {"display_name": "Bathroom", "state": "paused",
             "is_grouped": False, "group_members": []},
        ], default_zone=None)
        result = rs._handle_list_zones()
        names = [z["display_name"] for z in result["zones"]]
        # playing -> paused -> stopped
        self.assertEqual(names, ["Kitchen", "Bathroom", "Study"])

    def test_default_zone_match_via_group_member(self):
        """The default may be stored as an output name; a grouped zone whose
        member list includes that name should still show is_default=True."""
        rs = self._rs_with_zones([
            {"display_name": "Kitchen + Study", "state": "playing",
             "is_grouped": True,  "group_members": ["Kitchen", "Study"]},
        ], default_zone="Study")
        result = rs._handle_list_zones()
        self.assertTrue(result["zones"][0]["is_default"])


# ------------------------------------------------------------------ #
#  _handle_set_default_zone                                           #
# ------------------------------------------------------------------ #

class TestHandleSetDefaultZone(unittest.TestCase):
    def test_missing_zone_returns_error(self):
        rs = _bare_runtime()
        result = rs._handle_set_default_zone({})
        self.assertFalse(result["ok"])
        self.assertIn("required", result["error"])

    def test_happy_path(self):
        rs = _bare_runtime()
        result = rs._handle_set_default_zone({"zone": "Kitchen"})
        rs.roon_connection.set_default_zone.assert_called_once_with("Kitchen")
        self.assertTrue(result["ok"])
        self.assertEqual(result["zone"], "Kitchen")

    def test_zone_lookup_error_returns_error(self):
        rs = _bare_runtime()
        rs.resolve_zone_name = MagicMock(
            side_effect=ZoneLookupError("no such zone"),
        )
        result = rs._handle_set_default_zone({"zone": "Ghost"})
        self.assertFalse(result["ok"])
        self.assertIn("no such zone", result["error"])
        self.assertEqual(result["zone"], "Ghost")


if __name__ == "__main__":
    unittest.main()
