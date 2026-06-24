"""Pin the channel + payload contract for every backend WS emission.

The frontend reads channel constants from the agent. If a channel name
or required payload key drifts during a refactor, no test catches it —
the frontend just stops getting that update. These tests close the
push-path gap by exercising the production emission for each channel
and asserting both the channel name and the payload shape.

One ``WSCapture`` per test (in ``_runtime_fixtures``); each test drives
the smallest production path that emits on its target channel.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.constants import (  # noqa: E402
    CHANNEL_AGENT_OUTPUTS,
    CHANNEL_ANALYSIS_RUN_RESPONSE,
    CHANNEL_ANALYSIS_UPDATE,
    CHANNEL_QUEUE_UPDATES,
    CHANNEL_RATE_LIMIT,
    CHANNEL_USAGE_METRICS,
    CHANNEL_ZONE_SNAPSHOTS,
)

try:
    from tests._runtime_fixtures import (
        WSCapture,
        bare_runtime_for_zone_tests,
        make_mock_roon_connection,
    )
except ModuleNotFoundError:
    from _runtime_fixtures import (  # type: ignore[no-redef]
        WSCapture,
        bare_runtime_for_zone_tests,
        make_mock_roon_connection,
    )


# ── 1. CHANNEL_RATE_LIMIT — emit_rate_limit_banner ──────────────────


class TestRateLimitBannerEmission(unittest.TestCase):
    """``emit_rate_limit_banner`` is the only path to CHANNEL_RATE_LIMIT.
    The frontend banner manager keys on every field this payload carries."""

    def test_emits_on_rate_limit_channel_with_full_payload(self):
        from app.llm.rate_limit import emit_rate_limit_banner

        capture = WSCapture()
        emit_rate_limit_banner(
            capture,
            agent_name="Coordinator",
            retriable=True,
            error_text="429 Too Many Requests",
            retry_in_seconds=42,
            attempt=1,
            max_retries=3,
            can_override=False,
        )

        [payload] = capture.payloads_on(CHANNEL_RATE_LIMIT)
        # Frontend's useChatBannerManager keys on these specific fields.
        self.assertEqual(payload["agent_name"], "Coordinator")
        self.assertTrue(payload["active"])
        self.assertTrue(payload["retriable"])
        self.assertEqual(payload["error"], "429 Too Many Requests")
        self.assertEqual(payload["retry_in_seconds"], 42)
        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["max_retries"], 3)
        self.assertFalse(payload["can_override"])
        # display_seconds depends on retriable: 0 when retriable, 5 otherwise.
        self.assertEqual(payload["display_seconds"], 0)

    def test_non_retriable_uses_5_second_display(self):
        from app.llm.rate_limit import emit_rate_limit_banner

        capture = WSCapture()
        emit_rate_limit_banner(
            capture,
            agent_name="Arbiter",
            retriable=False,
            error_text="quota exceeded",
        )

        [payload] = capture.payloads_on(CHANNEL_RATE_LIMIT)
        self.assertEqual(payload["display_seconds"], 5)
        self.assertFalse(payload["retriable"])

    def test_no_emission_when_ws_send_fn_is_none(self):
        from app.llm.rate_limit import emit_rate_limit_banner

        # Production code skips silently in CLI mode (no ws_send_fn).
        # Documenting that behaviour here so a refactor doesn't suddenly
        # raise instead.
        emit_rate_limit_banner(
            None, agent_name="Coordinator",
            retriable=True, error_text="x",
        )


# ── 2. CHANNEL_ZONE_SNAPSHOTS + CHANNEL_QUEUE_UPDATES ───────────────


def _make_zone_runtime(zones, aliases=None, target_zone="Living Room"):
    aliases = aliases or {}
    rs, td = bare_runtime_for_zone_tests(with_connection=False)
    rs.roon_connection = make_mock_roon_connection(
        zones,
        outputs={
            oid: o
            for z in zones.values()
            for oid, o in ((out["output_id"], out) for out in z.get("outputs", []))
        },
        target_zone=target_zone,
    )
    capture = WSCapture()
    rs._ws_send_callback = capture
    rs._run_mode_getter = lambda: "ws"
    rs._get_alias_for_zone = lambda name: aliases.get(name)
    return rs, capture, td


def _zone(zone_id, display_name, **overrides):
    base = {
        "zone_id": zone_id,
        "display_name": display_name,
        "state": "playing",
        "outputs": [{"output_id": f"o-{zone_id}", "display_name": display_name, "zone_id": zone_id}],
        "now_playing": {"three_line": {"line1": "Song"}},
    }
    base.update(overrides)
    return base


def _fire_state_event(rs, zones_dict):
    rs._forward_roon_live_event({
        "type": "state",
        "event": "zones_changed",
        "changed_ids": list(zones_dict),
        "zones": list(zones_dict.values()),
    })


class TestZoneSnapshotEmission(unittest.TestCase):
    """State events from Roon produce a full zone-state snapshot on
    CHANNEL_ZONE_SNAPSHOTS. The snapshot carries everything the
    frontend needs to render zone cards."""

    def test_state_event_emits_snapshot(self):
        rs, capture, td = _make_zone_runtime({"z1": _zone("z1", "Living Room")})
        try:
            _fire_state_event(rs, {"z1": _zone("z1", "Living Room")})
            snapshots = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(len(snapshots), 1)
            [zone] = snapshots[0]["data"]["zones"]
            self.assertEqual(zone["zone_id"], "z1")
            self.assertEqual(zone["display_name"], "Living Room")
            self.assertEqual(zone["state"], "playing")
        finally:
            if td is not None:
                td.cleanup()

    def test_snapshot_includes_alias_and_volume(self):
        zones = {
            "z1": _zone("z1", "Living Room", outputs=[{
                "output_id": "o1",
                "display_name": "LR",
                "zone_id": "z1",
                "volume": {"value": 42, "type": "number"},
            }]),
        }
        rs, capture, td = _make_zone_runtime(zones, aliases={"Living Room": "Lounge"})
        try:
            _fire_state_event(rs, zones)
            [snapshot] = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            [zone] = snapshot["data"]["zones"]
            self.assertEqual(zone["zone_alias"], "Lounge")
            self.assertEqual(zone["outputs_volume"][0]["value"], 42)
        finally:
            if td is not None:
                td.cleanup()

    def test_stopped_with_queue_remaps_to_paused(self):
        zones = {"z1": _zone("z1", "Living Room",
                              state="stopped", queue_items_remaining=3)}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            _fire_state_event(rs, zones)
            [snapshot] = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(snapshot["data"]["zones"][0]["state"], "paused")
        finally:
            if td is not None:
                td.cleanup()

    def test_stopped_with_empty_queue_stays_stopped(self):
        zones = {"z1": _zone("z1", "Living Room",
                              state="stopped", queue_items_remaining=0)}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            _fire_state_event(rs, zones)
            [snapshot] = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(snapshot["data"]["zones"][0]["state"], "stopped")
        finally:
            if td is not None:
                td.cleanup()

    def test_identical_state_events_emit_only_once(self):
        zones = {"z1": _zone("z1", "Living Room")}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            _fire_state_event(rs, zones)
            _fire_state_event(rs, zones)
            snapshots = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(len(snapshots), 1)
        finally:
            if td is not None:
                td.cleanup()

    def test_zone_vanishing_from_api_is_absent_from_next_snapshot(self):
        zones = {
            "z1": _zone("z1", "Living Room"),
            "z2": _zone("z2", "Kitchen"),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            _fire_state_event(rs, zones)
            del rs.roon_connection.api.zones["z2"]
            _fire_state_event(rs, {"z1": zones["z1"]})

            snapshots = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(len(snapshots), 2)
            zone_ids = [z["zone_id"] for z in snapshots[1]["data"]["zones"]]
            self.assertEqual(zone_ids, ["z1"])
        finally:
            if td is not None:
                td.cleanup()


class TestZoneSnapshotOnLabelChange(unittest.TestCase):
    def test_label_change_emits_snapshot_with_new_alias(self):
        zones = {"z1": _zone("z1", "Living Room")}
        rs, capture, td = _make_zone_runtime(zones, aliases={"Living Room": "Lounge"})
        try:
            rs._broadcast_zone_labels("Living Room")
            [snapshot] = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            [zone] = snapshot["data"]["zones"]
            self.assertEqual(zone["zone_alias"], "Lounge")
        finally:
            if td is not None:
                td.cleanup()


class TestSeekChangedInfersPlayingState(unittest.TestCase):
    """zones_seek_changed only fires during active playback, so we patch
    api.zones[state] to ``playing`` when seek events arrive — Roon
    skips the zones_changed for paused→playing after group/ungroup."""

    def test_seek_changed_promotes_paused_to_playing(self):
        zones = {
            "z1": _zone(
                "z1", "Group",
                state="paused",
                queue_items_remaining=1,
                seek_position=15,
            ),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            rs._forward_roon_live_event({
                "type": "state",
                "event": "zones_seek_changed",
                "changed_ids": ["z1"],
                "zones": [zones["z1"]],
            })

            snapshots = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(len(snapshots), 1)
            [zone] = snapshots[0]["data"]["zones"]
            self.assertEqual(zone["state"], "playing")
            self.assertEqual(zone["seek_position"], 15)
        finally:
            if td is not None:
                td.cleanup()

    def test_seek_changed_does_not_revert_stopped_to_playing(self):
        # Track-end: a final seek event after state→stopped must not
        # revert the stop. Only paused is promoted.
        zones = {
            "z1": _zone(
                "z1", "Living Room",
                state="stopped",
                queue_items_remaining=0,
                seek_position=240,
            ),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            rs._forward_roon_live_event({
                "type": "state",
                "event": "zones_seek_changed",
                "changed_ids": ["z1"],
                "zones": [zones["z1"]],
            })
            # api.zones state must remain stopped after the seek tick.
            self.assertEqual(rs.roon_connection.api.zones["z1"]["state"], "stopped")
        finally:
            if td is not None:
                td.cleanup()

    def test_seek_changed_leaves_already_playing_alone(self):
        # Does nothing when the cached state is already correct.
        zones = {
            "z1": _zone("z1", "Living Room", state="playing", seek_position=10),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            rs._forward_roon_live_event({
                "type": "state",
                "event": "zones_seek_changed",
                "changed_ids": ["z1"],
                "zones": [zones["z1"]],
            })
            [zone] = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)[0]["data"]["zones"]
            self.assertEqual(zone["state"], "playing")
        finally:
            if td is not None:
                td.cleanup()

    def test_zones_changed_does_not_apply_seek_inference(self):
        # zones_changed (not zones_seek_changed) carries the full state
        # including ``state``; trust it as-is, don't promote.
        zones = {
            "z1": _zone(
                "z1", "Group",
                state="paused",
                queue_items_remaining=1,
                seek_position=15,
            ),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            rs._forward_roon_live_event({
                "type": "state",
                "event": "zones_changed",
                "changed_ids": ["z1"],
                "zones": [zones["z1"]],
            })

            [zone] = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)[0]["data"]["zones"]
            self.assertEqual(zone["state"], "paused")
        finally:
            if td is not None:
                td.cleanup()


class TestZoneSnapshotOnConnect(unittest.TestCase):
    def test_initial_snapshot_reflects_current_zones(self):
        zones = {
            "z1": _zone("z1", "Living Room"),
            "z2": _zone("z2", "Kitchen", state="paused"),
        }
        rs, _capture, td = _make_zone_runtime(zones)
        try:
            event = rs.get_initial_zone_snapshot()
            ids = sorted(z["zone_id"] for z in event["data"]["zones"])
            self.assertEqual(ids, ["z1", "z2"])
        finally:
            if td is not None:
                td.cleanup()

    def test_initial_snapshot_includes_stopped_with_queue(self):
        zones = {"z1": _zone("z1", "Living Room",
                              state="stopped", queue_items_remaining=3)}
        rs, _capture, td = _make_zone_runtime(zones)
        try:
            event = rs.get_initial_zone_snapshot()
            self.assertEqual(event["data"]["zones"][0]["state"], "paused")
        finally:
            if td is not None:
                td.cleanup()


class TestQueueEventEmission(unittest.TestCase):
    """Queue events emit on CHANNEL_QUEUE_UPDATES for the queue modal,
    and re-emit the zone snapshot so the FE picks up state changes
    that Roon delivers alongside (the transport subscription doesn't
    always fire a follow-up zones_changed)."""

    def test_queue_event_emits_on_queue_channel(self):
        zones = {"z1": _zone("z1", "Living Room")}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            rs._forward_roon_live_event({
                "type": "queue",
                "zone_id": "z1",
                "data": {"items": [{"queue_item_id": 100, "title": "Track A"}]},
            })

            [queue] = capture.payloads_on(CHANNEL_QUEUE_UPDATES)
            self.assertEqual(queue["zone_id"], "z1")
            self.assertEqual(queue["zone_display_name"], "Living Room")
            self.assertEqual(queue["items"][0]["title"], "Track A")
        finally:
            if td is not None:
                td.cleanup()

    def test_queue_event_emits_fresh_snapshot_with_updated_zone(self):
        # During group/ungroup, Roon's first state event reports
        # everything stopped; the real state arrives via subsequent
        # queue events. Without re-snapshotting on queue events the
        # FE renders the all-stopped transient.
        zones = {
            "z1": _zone(
                "z1", "Group",
                state="stopped",
                queue_items_remaining=0,
                now_playing={},
            ),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            # Mutate api.zones to mirror what Roon does between the
            # transient state event and the queue event.
            zones["z1"]["state"] = "paused"
            zones["z1"]["queue_items_remaining"] = 1
            zones["z1"]["queue_time_remaining"] = 198
            zones["z1"]["now_playing"] = {
                "three_line": {"line1": "Real Track", "line2": "Artist", "line3": "Album"},
                "length": 240,
                "image_key": "img-real",
            }

            rs._forward_roon_live_event({
                "type": "queue",
                "zone_id": "z1",
                "data": {"items": [{"queue_item_id": 100, "title": "Real Track"}]},
            })

            snapshots = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(len(snapshots), 1)
            [zone] = snapshots[0]["data"]["zones"]
            self.assertEqual(zone["state"], "paused")
            self.assertEqual(zone["queue_items_remaining"], 1)
            self.assertEqual(zone["now_playing"]["line1"], "Real Track")
            self.assertEqual(zone["image_key"], "img-real")
        finally:
            if td is not None:
                td.cleanup()

    def test_queue_event_refreshes_zones_when_zone_state_is_stopped(self):
        # A queue event on a "stopped" zone is the signal that
        # api.zones is on the group/ungroup transient — refresh via
        # /get_zones before snapshotting. Tests patch the spawn to
        # run inline; production uses the asyncio executor to avoid
        # deadlocking against Roon's WS receive thread.
        zones = {
            "z1": _zone(
                "z1", "Group",
                state="stopped",
                queue_items_remaining=0,
                now_playing={},
            ),
        }
        rs, capture, td = _make_zone_runtime(zones)
        try:
            # Simulate _get_zones returning the post-settlement state
            # that the transport subscription never delivered.
            def fake_get_zones():
                return {
                    "z1": {
                        "zone_id": "z1",
                        "display_name": "Group",
                        "state": "paused",
                        "queue_items_remaining": 1,
                        "queue_time_remaining": 198,
                        "now_playing": {
                            "three_line": {"line1": "Real Track", "line2": "Artist", "line3": "Album"},
                            "length": 198,
                            "image_key": "img-real",
                        },
                        "outputs": [{"display_name": "Group"}],
                    },
                }
            rs.roon_connection.api._get_zones = fake_get_zones
            # Drive the worker inline so the test stays synchronous.
            rs._spawn_zone_refresh = rs._refresh_zones_and_emit_snapshot

            rs._forward_roon_live_event({
                "type": "queue",
                "zone_id": "z1",
                "data": {"items": [{"queue_item_id": 100, "title": "Real Track"}]},
            })

            snapshots = capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)
            self.assertEqual(len(snapshots), 1)
            [zone] = snapshots[0]["data"]["zones"]
            self.assertEqual(zone["state"], "paused")
            self.assertEqual(zone["queue_items_remaining"], 1)
            self.assertEqual(zone["now_playing"]["line1"], "Real Track")
        finally:
            if td is not None:
                td.cleanup()

    def test_queue_event_skips_refresh_when_zone_state_is_not_stopped(self):
        # Normal playback doesn't need a refresh — api.zones is fresh
        # from the most recent zones_changed.
        zones = {"z1": _zone("z1", "Living Room", state="playing")}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            spawn_calls = {"n": 0}
            rs._spawn_zone_refresh = lambda: spawn_calls.__setitem__("n", spawn_calls["n"] + 1)

            rs._forward_roon_live_event({
                "type": "queue",
                "zone_id": "z1",
                "data": {"items": [{"queue_item_id": 100, "title": "Next Track"}]},
            })

            self.assertEqual(spawn_calls["n"], 0)
        finally:
            if td is not None:
                td.cleanup()

    def test_queue_event_dedups_snapshot_when_zone_unchanged(self):
        # Queue payload fires; snapshot is suppressed by the signature
        # dedup when nothing zone-side changed.
        zones = {"z1": _zone("z1", "Living Room")}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            # Prime the snapshot signature with the current zone state.
            _fire_state_event(rs, zones)
            self.assertEqual(len(capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)), 1)

            # A queue event without any zone-side changes: queue fires,
            # snapshot does NOT (same signature).
            rs._forward_roon_live_event({
                "type": "queue",
                "zone_id": "z1",
                "data": {"items": []},
            })
            self.assertEqual(len(capture.payloads_on(CHANNEL_ZONE_SNAPSHOTS)), 1)
            self.assertEqual(len(capture.payloads_on(CHANNEL_QUEUE_UPDATES)), 1)
        finally:
            if td is not None:
                td.cleanup()


class TestInitialQueueEvents(unittest.TestCase):
    """``get_initial_queue_events`` builds connect-time per-zone queue
    payloads, skipping zones with empty queues."""

    def test_skips_empty_queues(self):
        from unittest.mock import MagicMock
        rs, td = bare_runtime_for_zone_tests(with_connection=False)
        try:
            rs.roon_connection = MagicMock()
            rs.roon_connection.last_queue_events_by_zone = {
                "z1": {"data": {"items": [{"queue_item_id": 1}]}},
                "z2": {"data": {"items": []}},
            }
            rs.roon_connection.api.zones.get.side_effect = lambda zid, _default=None: {
                "z1": {"display_name": "Kitchen"},
                "z2": {"display_name": "Study"},
            }.get(zid, {})

            events = rs.get_initial_queue_events()
            self.assertEqual([e["zone_id"] for e in events], ["z1"])
        finally:
            if td is not None:
                td.cleanup()


class TestCliModeNoZoneEmissions(unittest.TestCase):
    def test_cli_mode_short_circuits_no_emissions(self):
        zones = {"z1": _zone("z1", "Living Room")}
        rs, capture, td = _make_zone_runtime(zones)
        try:
            rs._run_mode_getter = lambda: "cli"
            _fire_state_event(rs, zones)
            rs._forward_roon_live_event({
                "type": "queue",
                "zone_id": "z1",
                "data": {"items": []},
            })
            self.assertEqual(capture.calls, [])
        finally:
            if td is not None:
                td.cleanup()


# ── 3. CHANNEL_AGENT_OUTPUTS + CHANNEL_USAGE_METRICS ────────────────
#     (process_request fires both during a normal request)


try:
    from tests._runtime_fixtures import make_request_runtime as _make_request_runtime
except ModuleNotFoundError:
    from _runtime_fixtures import make_request_runtime as _make_request_runtime


class TestProcessRequestEmissions(unittest.TestCase):
    """A successful request must emit the request-start AGENT_OUTPUTS
    marker, the per-request USAGE_METRICS, and the request-complete
    AGENT_OUTPUTS event. The frontend's RequestSummaryPanel keys on
    request_id_assignment + request_complete being a matched pair."""

    def test_text_response_emits_request_markers_and_usage_metrics(self):
        from app.coordinator.request_flow import process_request
        from app.llm.client import LLMResponse

        runtime = _make_request_runtime()

        # Mock the LLM client to return a text response immediately
        # (no tool calls). Real tool_loop machinery still runs.
        async def _fake_completion(messages, tools=None):
            return LLMResponse(
                text="Hello",
                tool_calls=[],
                usage={
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "total_tokens": 105,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cost_usd": 0.0001,
                },
            )

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _fake_completion

        try:
            from tests._runtime_fixtures import wire_ws_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_ws_test_bus  # type: ignore[no-redef]
        capture = WSCapture()
        bus = wire_ws_test_bus(capture, runtime)
        # conftest stubs RequestLogger with NullRequestLogger and redirects
        # SWARPIUS_DATA_DIR to a temp dir, so process_request runs without
        # touching real logs.
        process_request(
            runtime=runtime,
            user_input="hi",
            cancel_event=None,
            event_bus=bus,
            run_mode_label="ws",
        )

        # request_id_assignment fires at request start.
        agent_events = capture.payloads_on(CHANNEL_AGENT_OUTPUTS)
        event_types = [e.get("event_type") for e in agent_events]
        self.assertIn("request_id_assignment", event_types)
        self.assertIn("request_complete", event_types)

        start = next(e for e in agent_events if e["event_type"] == "request_id_assignment")
        self.assertEqual(start["source"], "[Request]")
        self.assertEqual(start["user_input"], "hi")
        self.assertTrue(start["request_id"].startswith("rq-"))
        self.assertEqual(start["coordinator_model"], "dummy/dummy-model")

        # CHANNEL_USAGE_METRICS fires once per request, after the loop.
        [usage] = capture.payloads_on(CHANNEL_USAGE_METRICS)
        self.assertEqual(usage["agent"], "Coordinator")
        self.assertEqual(usage["source"], "provider")
        self.assertEqual(usage["call"]["input_tokens"], 100)
        self.assertEqual(usage["call"]["output_tokens"], 5)
        self.assertEqual(usage["call"]["total_tokens"], 105)
        # Required nested groups for the frontend usage panel.
        for key in (
            "session_totals",
            "session_breakdown",
            "tokens_per_minute",
            "tokens_per_minute_breakdown",
            "requests_per_minute",
        ):
            self.assertIn(key, usage)

    def test_failed_request_emits_request_complete_with_error(self):
        """A failed request must still close its lifecycle with a
        request_complete (status=error, carrying the reason), so it surfaces as a
        failed request — e.g. in Session Requests — rather than vanishing."""
        from app.coordinator.request_flow import process_request

        runtime = _make_request_runtime()

        async def _failing_completion(messages, tools=None):
            raise RuntimeError("boom")

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _failing_completion

        try:
            from tests._runtime_fixtures import wire_ws_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_ws_test_bus  # type: ignore[no-redef]
        capture = WSCapture()
        bus = wire_ws_test_bus(capture, runtime)
        process_request(
            runtime=runtime,
            user_input="hi",
            cancel_event=None,
            event_bus=bus,
            run_mode_label="ws",
        )

        agent_events = capture.payloads_on(CHANNEL_AGENT_OUTPUTS)
        complete = next(
            (e for e in agent_events if e.get("event_type") == "request_complete"), None,
        )
        self.assertIsNotNone(complete, "failed request must still emit request_complete")
        self.assertEqual(complete["status"], "error")
        self.assertTrue(complete.get("error"), "request_complete must carry the failure reason")

    def test_request_id_assignment_echoes_client_msg_id(self):
        """``request_id_assignment`` echoes ``client_msg_id`` so the
        FE can pair badges by direct lookup."""
        from app.coordinator.request_flow import process_request
        from app.llm.client import LLMResponse

        runtime = _make_request_runtime()

        async def _fake_completion(messages, tools=None):
            return LLMResponse(
                text="ack",
                tool_calls=[],
                usage={
                    "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0, "cost_usd": 0.0,
                },
            )

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _fake_completion

        try:
            from tests._runtime_fixtures import wire_ws_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_ws_test_bus  # type: ignore[no-redef]
        capture = WSCapture()
        bus = wire_ws_test_bus(capture, runtime)
        process_request(
            runtime=runtime,
            user_input="hi",
            cancel_event=None,
            event_bus=bus,
            run_mode_label="ws",
            client_msg_id="fe-uuid-123",
        )

        agent_events = capture.payloads_on(CHANNEL_AGENT_OUTPUTS)
        start = next(e for e in agent_events if e.get("event_type") == "request_id_assignment")
        self.assertEqual(start["client_msg_id"], "fe-uuid-123")

    def test_request_id_assignment_omits_client_msg_id_when_unset(self):
        """Callers that don't supply a ``client_msg_id`` produce an
        assignment payload without the field at all."""
        from app.coordinator.request_flow import process_request
        from app.llm.client import LLMResponse

        runtime = _make_request_runtime()

        async def _fake_completion(messages, tools=None):
            return LLMResponse(
                text="ack", tool_calls=[],
                usage={
                    "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0, "cost_usd": 0.0,
                },
            )

        runtime.llm_client = MagicMock()
        runtime.llm_client.model = "dummy/dummy-model"
        runtime.llm_client.completion = _fake_completion

        try:
            from tests._runtime_fixtures import wire_ws_test_bus
        except ModuleNotFoundError:
            from _runtime_fixtures import wire_ws_test_bus  # type: ignore[no-redef]
        capture = WSCapture()
        bus = wire_ws_test_bus(capture, runtime)
        process_request(
            runtime=runtime,
            user_input="hi",
            cancel_event=None,
            event_bus=bus,
            run_mode_label="cli",
        )

        agent_events = capture.payloads_on(CHANNEL_AGENT_OUTPUTS)
        start = next(e for e in agent_events if e.get("event_type") == "request_id_assignment")
        self.assertNotIn("client_msg_id", start)


# ── 4. CHANNEL_ANALYSIS_RUN_RESPONSE + CHANNEL_ANALYSIS_UPDATE ──────


class _FakeWebSocket:
    """Captures ``send()`` calls as parsed (channel, payload) tuples,
    mirroring the wire format of ``_ws_send_to_client``."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, message: str) -> None:
        decoded = json.loads(message)
        self.sent.append((decoded["channel"], decoded["payload"]))


class TestAnalysisBackgroundScanEmissions(unittest.TestCase):
    """``_background_scan`` and ``_background_rerun`` are the *only* paths
    to CHANNEL_ANALYSIS_RUN_RESPONSE / CHANNEL_ANALYSIS_UPDATE. The
    frontend's RequestLogsView keys on the ``completed: true`` follow-up
    plus the ``list_refreshed`` / ``list_entry_updated`` event types."""

    def test_scan_success_emits_completed_response_and_list_refreshed(self):
        from app.io.websocket_flow import _background_scan

        ws = _FakeWebSocket()

        with patch(
            "app.analysis.browser.scan_and_analyse",
            return_value={
                "ok": True,
                "scanned": 3,
                "analysed": 1,
                "skipped": 2,
            },
        ), patch(
            "app.analysis.browser.list_analysed_conversations",
            return_value={
                "conversations": [{"date": "2026-04-25", "id": "c01"}],
                "models": ["anthropic/claude-sonnet-4-6"],
            },
        ):
            asyncio.run(_background_scan(ws, Path("/fake/logs"), "rq-c01-0001"))

        # Two emissions on success: one response, one list update.
        channels = [c for c, _ in ws.sent]
        self.assertEqual(channels.count(CHANNEL_ANALYSIS_RUN_RESPONSE), 1)
        self.assertEqual(channels.count(CHANNEL_ANALYSIS_UPDATE), 1)

        response = next(p for c, p in ws.sent if c == CHANNEL_ANALYSIS_RUN_RESPONSE)
        self.assertEqual(response["request_id"], "rq-c01-0001")
        self.assertTrue(response["completed"])
        self.assertTrue(response["ok"])
        self.assertEqual(response["scanned"], 3)

        update = next(p for c, p in ws.sent if c == CHANNEL_ANALYSIS_UPDATE)
        self.assertEqual(update["type"], "list_refreshed")
        self.assertEqual(len(update["conversations"]), 1)
        self.assertEqual(update["models"], ["anthropic/claude-sonnet-4-6"])


if __name__ == "__main__":
    unittest.main()
