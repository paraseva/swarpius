"""Tests for the ``stop`` transport action on RoonActionTool.

After the StopMarkerCoordinator refactor, ``_stop_action`` is a thin
shim that:

1. Calls ``coordinator.dispatch_stop(zone=...)``
2. Translates the StopResult into a RoonActionToolOutputSchema
3. Issues ``playback_control(control="pause", zone=...)`` when the
   coordinator signals ``use_pause_fallback`` (disabled mode and
   missing-marker scenarios — the agent falls back to plain pause,
   matching pre-stop-feature semantics)

These tests target that shim. Coordinator state machine behaviour is
covered separately in ``test_stop_marker_coordinator.py``.

A small integration block at the end exercises the tool + coordinator
end-to-end against a fake connection, confirming the success path
stays connected.
"""

import asyncio
import os
import unittest
from typing import Any, List, Optional
from unittest.mock import MagicMock

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
from tools.roon_action import (  # noqa: E402
    RoonActionTool,
    RoonActionToolConfig,
    RoonActionToolInputSchema,
)


def _make_tool(coord: Any, conn: Any) -> RoonActionTool:
    tool = RoonActionTool(config=RoonActionToolConfig(
        resolve_zone=lambda z: z,
        roon_connection=conn,
        stop_marker_coordinator_getter=lambda: coord,
    ))
    return tool


def _stop(tool: RoonActionTool, zone: Optional[str] = None) -> Any:
    params = RoonActionToolInputSchema(action="stop", zone=zone)
    return asyncio.run(tool.run_async(params))


# ─────────────────────────────────────────────────────────────────────
# Shim behaviour — coordinator is mocked so only the translation logic
# in _stop_action is under test.
# ─────────────────────────────────────────────────────────────────────


class TestStopShimRoutesThroughCoordinator(unittest.TestCase):
    """Translation-logic tests for ``_stop_action``.

    The shim's contract is: route the call to coordinator.dispatch_stop,
    translate the StopResult to a RoonActionToolOutputSchema, and decide
    whether to also issue playback_control(pause). The coordinator is
    a collaborator with full state-machine coverage in
    test_stop_marker_coordinator.py (15 tests) AND end-to-end coverage
    against the real coordinator below (TestStopEndToEndWithRealCoordinator).
    MagicMock'ing it here is at the right boundary for THIS scope —
    we're testing the four StopResult → output-and-side-effect branches,
    not re-asserting on the coordinator's internal walk.
    """

    def test_real_stop_success_yields_successful_no_pause(self):
        coord = MagicMock()
        coord.dispatch_stop.return_value = StopResult(
            succeeded=True, use_pause_fallback=False,
        )
        conn = MagicMock()
        tool = _make_tool(coord, conn)

        result = _stop(tool, zone="Living Room")

        coord.dispatch_stop.assert_called_once_with(zone="Living Room")
        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)
        # Real-stop path must not also issue a pause — that would
        # double-stop on top of the silent-marker dispatch.
        conn.playback_control.assert_not_called()

    def test_pause_fallback_issues_pause_and_reports_success(self):
        # Coordinator signals "feature unavailable / disabled" — the
        # tool degrades to pause and reports SUCCESSFUL with no error,
        # matching pre-stop-feature semantics.
        coord = MagicMock()
        coord.dispatch_stop.return_value = StopResult(
            succeeded=False, use_pause_fallback=True,
        )
        conn = MagicMock()
        tool = _make_tool(coord, conn)

        result = _stop(tool, zone="Kitchen")

        coord.dispatch_stop.assert_called_once_with(zone="Kitchen")
        conn.playback_control.assert_called_once_with(
            control="pause", zone="Kitchen",
        )
        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)

    def test_terminal_failure_reports_error_no_pause(self):
        # Coordinator was available but the dispatch (post-re-init)
        # still failed — surface the error so it lands in the chat
        # banner; do not silently fall back to pause.
        coord = MagicMock()
        coord.dispatch_stop.return_value = StopResult(
            succeeded=False, use_pause_fallback=False,
            error="track drill returned an unexpected list",
        )
        conn = MagicMock()
        tool = _make_tool(coord, conn)

        result = _stop(tool, zone="Bedroom")

        self.assertIn("FAILED", result.result)
        self.assertEqual(
            result.error, "track drill returned an unexpected list",
        )
        conn.playback_control.assert_not_called()

    def test_no_coordinator_falls_back_to_plain_pause(self):
        # Defensive path: if the runtime hasn't built a coordinator yet
        # (e.g. very early test wiring or pre-Roon-init), the tool just
        # pauses. No exception, no error.
        conn = MagicMock()
        tool = _make_tool(coord=None, conn=conn)

        result = _stop(tool, zone="Office")

        conn.playback_control.assert_called_once_with(
            control="pause", zone="Office",
        )
        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)


# ─────────────────────────────────────────────────────────────────────
# End-to-end: the stop shim + coordinator against a fake connection,
# confirming the success path stays connected.
# ─────────────────────────────────────────────────────────────────────


class _IntegrationConnectionFake:
    """Roon connection fake for the end-to-end stop walk."""

    MARKER_KEY = "131:0"
    PLAY_NOW_KEY = "200:0"

    def __init__(self, marker_title: str, marker_present: bool = True) -> None:
        self.session_manager = BrowseSessionManager()
        self.marker_title = marker_title
        self.marker_present = marker_present
        self.browse_aux_calls: List[dict] = []
        self.set_auto_radio_calls: List[dict] = []
        self.playback_calls: List[dict] = []

    def browse_core(
        self,
        aux: dict,
        zone: Optional[str] = None,
        session_key: Optional[str] = None,
        update_current: bool = True,
    ) -> RoonCoreResultsSchema:
        _ = (zone, session_key, update_current)
        self.browse_aux_calls.append(dict(aux))
        if "input" in aux and aux.get("pop_all"):
            return self._search_response()
        item_key = aux.get("item_key")
        if item_key == self.MARKER_KEY:
            return RoonCoreResultsSchema(
                items=[
                    RoonCoreItemSchema(
                        title="Play Now", item_key=self.PLAY_NOW_KEY,
                        hint="Action",
                    ),
                ],
                list=RoonCoreListSchema(
                    count=1, hint="action_list", title=self.marker_title,
                ),
            )
        if item_key == self.PLAY_NOW_KEY:
            return RoonCoreResultsSchema(
                items=[],
                list=RoonCoreListSchema(count=0, hint=None, title=""),
            )
        raise RuntimeError(f"unexpected item_key={item_key}")

    def _search_response(self) -> RoonCoreResultsSchema:
        items = (
            [
                RoonCoreItemSchema(
                    title=self.marker_title, item_key=self.MARKER_KEY,
                    hint="action_list",
                ),
            ]
            if self.marker_present else
            [
                RoonCoreItemSchema(
                    title="Some Other", item_key="other:0", hint="list",
                ),
            ]
        )
        return RoonCoreResultsSchema(
            items=items,
            list=RoonCoreListSchema(count=len(items), hint=None, title=""),
        )

    def find_item_by_field(
        self, items: List[RoonCoreItemSchema],
        field_name: str, field_value: str,
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

    def playback_control(
        self, control: str, zone: Optional[str] = None,
    ) -> None:
        self.playback_calls.append({"control": control, "zone": zone})


class TestStopEndToEndWithRealCoordinator(unittest.TestCase):
    """The shim and the coordinator wire together correctly: a real
    coordinator initialised against a fake connection drives a real
    stop dispatch through the real RoonActionTool."""

    def _build(self, marker_present: bool = True):
        conn = _IntegrationConnectionFake(
            marker_title=STOP_MARKER_TITLE,
            marker_present=marker_present,
        )
        coord = StopMarkerCoordinator(
            connection=conn,
            marker_title=STOP_MARKER_TITLE,
            broadcast_state=lambda: None,
        )
        coord.initialise()
        tool = _make_tool(coord, conn)
        return conn, coord, tool

    def test_present_marker_runs_full_dispatch_and_disables_auto_radio(self):
        conn, coord, tool = self._build(marker_present=True)
        # Cleared between init and dispatch so we observe only the
        # dispatch calls on the assertions below.
        conn.browse_aux_calls.clear()
        conn.set_auto_radio_calls.clear()

        result = _stop(tool, zone="Living Room")

        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)
        # Two browse calls: drill cached track key, drill Play Now.
        item_keys = [
            aux.get("item_key") for aux in conn.browse_aux_calls
            if "item_key" in aux
        ]
        self.assertEqual(
            item_keys,
            [conn.MARKER_KEY, conn.PLAY_NOW_KEY],
        )
        # Auto-radio disabled exactly once, after dispatch succeeded.
        self.assertEqual(
            conn.set_auto_radio_calls,
            [{"auto_radio": False, "zone": "Living Room"}],
        )
        # No pause-fallback path on the success branch.
        self.assertEqual(conn.playback_calls, [])

    def test_absent_marker_falls_back_to_pause(self):
        # Marker isn't in the library at all; the stop call should
        # silently degrade to pause without a banner-worthy error.
        conn, coord, tool = self._build(marker_present=False)

        result = _stop(tool, zone="Kitchen")

        self.assertFalse(coord.available)
        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)
        self.assertEqual(
            conn.playback_calls,
            [{"control": "pause", "zone": "Kitchen"}],
        )
        # No auto-radio change on the pause path.
        self.assertEqual(conn.set_auto_radio_calls, [])


# ─────────────────────────────────────────────────────────────────────
# Custom marker title — verified through the real coordinator init walk
# ─────────────────────────────────────────────────────────────────────


class TestCustomMarkerTitleEndToEnd(unittest.TestCase):
    """The marker title is configurable via ROON_STOP_MARKER_TITLE.
    With a real coordinator, the search call uses that title."""

    def setUp(self):
        self._prev = os.environ.get("ROON_STOP_MARKER_TITLE")
        os.environ["ROON_STOP_MARKER_TITLE"] = "My Custom Stop Marker"
        from app.settings import reset_settings_for_tests
        reset_settings_for_tests()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ROON_STOP_MARKER_TITLE", None)
        else:
            os.environ["ROON_STOP_MARKER_TITLE"] = self._prev
        from app.settings import reset_settings_for_tests
        reset_settings_for_tests()

    def test_custom_title_used_in_search(self):
        from app.settings import get_settings
        title = get_settings().stop_marker_title
        self.assertEqual(title, "My Custom Stop Marker")

        conn = _IntegrationConnectionFake(marker_title=title)
        coord = StopMarkerCoordinator(
            connection=conn,
            marker_title=title,
            broadcast_state=lambda: None,
        )
        coord.initialise()

        searches = [
            aux for aux in conn.browse_aux_calls if aux.get("pop_all")
        ]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]["input"], "My Custom Stop Marker")


if __name__ == "__main__":
    unittest.main()
