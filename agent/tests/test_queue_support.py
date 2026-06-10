"""Tests for queue subscription lifecycle, queue item transform,
and the queue status tool."""

import asyncio
import unittest
from unittest.mock import MagicMock

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.roon.tag_expansion import transform_queue_items_to_result_shape
from roon_core.events import RoonEventsMixin
from tools.roon_status import RoonStatusTool, RoonStatusToolConfig, RoonStatusToolInputSchema

# ── Fixtures ────────────────────────────────────────────────────────


def _raw_queue_item(queue_item_id, title, artist, album, length=200):
    """Build a raw Roon queue item as returned by the queue subscription."""
    return {
        "queue_item_id": queue_item_id,
        "length": length,
        "image_key": f"img_{queue_item_id}",
        "one_line": {"line1": f"{title} - {artist}"},
        "two_line": {"line1": title, "line2": artist},
        "three_line": {"line1": title, "line2": artist, "line3": album},
    }


SAMPLE_QUEUE_ITEMS = [
    _raw_queue_item(83581, "I'm Good (Blue)", "David Guetta / Bebe Rexha", "I'm Good (Blue)", 219),
    _raw_queue_item(83536, "Cry For You", "September", "Hard2Beat Club Anthems 2008", 192),
    _raw_queue_item(83552, "Tell Me", "INNA", "E.T.", 228),
    _raw_queue_item(83568, "Believe", "Cher", "Believe", 239),
]


def _make_zone(display_name="Living Room", zone_id="zone-1", state="playing"):
    return {
        "display_name": display_name,
        "zone_id": zone_id,
        "state": state,
        "outputs": [{"display_name": display_name, "output_id": f"output-{zone_id}"}],
    }


# ── Queue item transform ───────────────────────────────────────────


class TestTransformQueueItems(unittest.TestCase):
    """Test transform_queue_items_to_result_shape maps Roon queue fields
    to the existing result item dict shape."""

    def test_basic_transform(self):
        result = transform_queue_items_to_result_shape(SAMPLE_QUEUE_ITEMS)
        self.assertEqual(len(result), 4)
        first = result[0]
        self.assertEqual(first["title"], "I'm Good (Blue)")
        self.assertEqual(first["extra_info"], "David Guetta / Bebe Rexha")
        self.assertEqual(first["group"], "I'm Good (Blue)")
        self.assertTrue(first["reference"].startswith("Q:"), "Queue refs should have Q: prefix")
        bare_ref = first["reference"][2:]
        self.assertEqual(len(bare_ref), 5)
        self.assertTrue(all(c in "0123456789abcdef" for c in bare_ref))
        self.assertEqual(first["queue_item_id"], 83581)

    def test_references_are_unique_random_hex(self):
        result = transform_queue_items_to_result_shape(SAMPLE_QUEUE_ITEMS)
        refs = [item["reference"] for item in result]
        self.assertEqual(len(refs), len(set(refs)), "References should be unique")

    def test_empty_input(self):
        self.assertEqual(transform_queue_items_to_result_shape([]), [])

    def test_missing_three_line_uses_empty_group(self):
        item = _raw_queue_item(1, "Track", "Artist", "Album")
        del item["three_line"]
        result = transform_queue_items_to_result_shape([item])
        self.assertEqual(result[0]["group"], "")

    def test_missing_two_line_uses_one_line(self):
        item = {
            "queue_item_id": 1,
            "length": 100,
            "image_key": None,
            "one_line": {"line1": "Track - Artist"},
        }
        result = transform_queue_items_to_result_shape([item])
        self.assertEqual(result[0]["title"], "Track - Artist")
        self.assertEqual(result[0]["extra_info"], "")


# ── Queue subscription lifecycle ────────────────────────────────────


class FakeEventsHost(RoonEventsMixin):
    """Minimal host for testing RoonEventsMixin methods."""

    def __init__(self):
        self.api = MagicMock()
        self.api.zones = {
            "zone-1": {"display_name": "Living Room", "zone_id": "zone-1"},
            "zone-2": {"display_name": "Kitchen", "zone_id": "zone-2"},
        }
        self.api._roonsocket = MagicMock()
        self.target_zone = "Living Room"
        self._event_listeners = []
        self._subscriptions_registered = False
        self._queue_subscribed_zones = set()
        self._queue_socket_id = None
        self._queue_items_cache = {}
        self._queue_ref_maps = {}
        self.last_state_event = None
        self.last_queue_event = None
        self.last_queue_events_by_zone = {}


class TestQueueSubscriptionLifecycle(unittest.TestCase):

    def test_startup_subscribes_all_zones(self):
        host = FakeEventsHost()
        host._ensure_live_subscriptions()
        self.assertEqual(host._queue_subscribed_zones, {"zone-1", "zone-2"})
        self.assertEqual(host.api.register_queue_callback.call_count, 2)

    def test_zones_added_subscribes_new_zone(self):
        host = FakeEventsHost()
        host._ensure_live_subscriptions()
        host.api.register_queue_callback.reset_mock()

        host.api.zones["zone-3"] = {"display_name": "Bedroom", "zone_id": "zone-3"}
        host._on_state_event("zones_added", ["zone-3"])

        self.assertIn("zone-3", host._queue_subscribed_zones)
        host.api.register_queue_callback.assert_called_once()

    def test_zones_removed_cleans_up(self):
        host = FakeEventsHost()
        host._ensure_live_subscriptions()
        host.last_queue_events_by_zone["zone-2"] = {"data": {"items": []}}

        host._on_state_event("zones_removed", ["zone-2"])

        self.assertNotIn("zone-2", host._queue_subscribed_zones)
        self.assertNotIn("zone-2", host.last_queue_events_by_zone)

    def test_duplicate_subscribe_avoided(self):
        host = FakeEventsHost()
        host._ensure_live_subscriptions()
        initial_count = host.api.register_queue_callback.call_count

        host._on_state_event("zones_changed", ["zone-1"])
        self.assertEqual(host.api.register_queue_callback.call_count, initial_count)

    def test_reconnect_resubscribes_all_zones(self):
        host = FakeEventsHost()
        host._ensure_live_subscriptions()
        initial_count = host.api.register_queue_callback.call_count

        # Simulate reconnect — new socket object
        host.api._roonsocket = MagicMock()
        host._on_state_event("zones_changed", ["zone-1", "zone-2"])

        # Should have re-subscribed both zones
        self.assertEqual(
            host.api.register_queue_callback.call_count,
            initial_count + 2,
        )
        self.assertEqual(host._queue_subscribed_zones, {"zone-1", "zone-2"})

    def test_reconnect_clears_stale_subscriptions(self):
        host = FakeEventsHost()
        host._ensure_live_subscriptions()
        self.assertEqual(host._queue_subscribed_zones, {"zone-1", "zone-2"})

        # Simulate reconnect with only one zone
        host.api._roonsocket = MagicMock()
        host._on_state_event("zones_changed", ["zone-1"])

        # Only zone-1 should be in the set (zone-2 was stale)
        self.assertEqual(host._queue_subscribed_zones, {"zone-1"})


# ── Queue event storage ─────────────────────────────────────────────


class TestQueueEventStorage(unittest.TestCase):

    def test_queue_event_stored_by_zone(self):
        host = FakeEventsHost()
        host._on_queue_event({
            "zone_id": "zone-1",
            "items": SAMPLE_QUEUE_ITEMS,
        })
        self.assertIn("zone-1", host.last_queue_events_by_zone)
        stored = host.last_queue_events_by_zone["zone-1"]
        self.assertEqual(stored["data"]["items"], SAMPLE_QUEUE_ITEMS)

    def test_queue_event_without_zone_id_resolves_from_output(self):
        host = FakeEventsHost()
        host.api.outputs = {
            "output-1": {"zone_id": "zone-1"},
        }
        host._on_queue_event({
            "zone_or_output_id": "output-1",
            "items": SAMPLE_QUEUE_ITEMS,
        })
        self.assertIn("zone-1", host.last_queue_events_by_zone)

    def test_queue_callback_injects_zone_id(self):
        """Roon sends queue data with no zone_id — the closure must inject it."""
        host = FakeEventsHost()
        callback = host._make_queue_callback("zone-1")
        callback({"items": SAMPLE_QUEUE_ITEMS})
        self.assertIn("zone-1", host.last_queue_events_by_zone)
        stored = host.last_queue_events_by_zone["zone-1"]
        self.assertEqual(stored["data"]["items"], SAMPLE_QUEUE_ITEMS)

    def test_queue_callback_does_not_overwrite_existing_zone_id(self):
        """If zone_id is already present, don't overwrite it."""
        host = FakeEventsHost()
        callback = host._make_queue_callback("zone-1")
        callback({"zone_id": "zone-2", "items": SAMPLE_QUEUE_ITEMS})
        self.assertIn("zone-2", host.last_queue_events_by_zone)
        self.assertNotIn("zone-1", host.last_queue_events_by_zone)

    def test_differential_remove_updates_cache(self):
        """Roon sends changes with remove ops after tracks finish or skip."""
        host = FakeEventsHost()
        # Initial full list
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_QUEUE_ITEMS)})
        self.assertEqual(len(host._queue_items_cache["zone-1"]), 4)

        # Remove first item (track finished)
        host._on_queue_event({"zone_id": "zone-1", "changes": [
            {"operation": "remove", "index": 0, "count": 1},
        ]})
        cached = host._queue_items_cache["zone-1"]
        self.assertEqual(len(cached), 3)
        self.assertEqual(cached[0]["queue_item_id"], 83536)  # was position 2

        # Stored payload also has the reconstructed items
        stored = host.last_queue_events_by_zone["zone-1"]
        self.assertEqual(len(stored["data"]["items"]), 3)

    def test_differential_insert_updates_cache(self):
        """Roon sends changes with insert ops when tracks are queued."""
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_QUEUE_ITEMS)})

        new_track = _raw_queue_item(99999, "New Track", "New Artist", "New Album")
        host._on_queue_event({"zone_id": "zone-1", "changes": [
            {"operation": "insert", "index": 2, "items": [new_track]},
        ]})
        cached = host._queue_items_cache["zone-1"]
        self.assertEqual(len(cached), 5)
        self.assertEqual(cached[2]["queue_item_id"], 99999)

    def test_differential_without_prior_cache_gives_empty(self):
        """Changes without a prior full list should not crash."""
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "changes": [
            {"operation": "remove", "index": 0, "count": 1},
        ]})
        self.assertEqual(host._queue_items_cache.get("zone-1", []), [])


# ── Queue status tool ───────────────────────────────────────────────


class FakeRoonConnectionForQueue:
    def __init__(self, queue_items=None, zone=None):
        self._queue_items = queue_items or []
        self._zone = zone or _make_zone()

    def get_zone_snapshot(self, zone=None):
        return self._zone

    def get_zones_snapshot(self):
        return [self._zone]

    def get_queue_items(self, zone=None):
        return self._queue_items

    def get_queue_references(self, zone=None):
        return None


class TestQueueStatusTool(unittest.TestCase):

    def _tool(self, queue_items=None):
        self._stored_handles = {}
        counter = [0]

        def _fake_store_handle(payload):
            counter[0] += 1
            handle = f"que_{counter[0]:05d}"
            self._stored_handles[handle] = payload
            return handle

        tool = RoonStatusTool(config=RoonStatusToolConfig(
            resolve_zone=lambda z: z,
            store_handle=_fake_store_handle,
        ))
        tool.roon_connection = FakeRoonConnectionForQueue(queue_items=queue_items)
        return tool

    def test_queue_status_returns_compact_text(self):
        tool = self._tool(queue_items=SAMPLE_QUEUE_ITEMS)
        output = asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        self.assertIn("I'm Good (Blue)", output.result)
        self.assertIn("Cry For You", output.result)
        # References carry Q: prefix — check they're present as bracketed refs
        self.assertRegex(output.result, r"\[Q:[0-9a-f]{5}\]")

    def test_queue_status_empty_queue(self):
        tool = self._tool(queue_items=[])
        output = asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        self.assertIn("no queue", output.result.lower())

    def test_queue_status_full_list_no_truncation(self):
        """Queue should return ALL items, no truncation."""
        many_items = [
            _raw_queue_item(i, f"Track {i}", f"Artist {i}", f"Album {i}")
            for i in range(60)
        ]
        tool = self._tool(queue_items=many_items)
        output = asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        # All 60 tracks should be in the output
        self.assertIn("Track 0", output.result)
        self.assertIn("Track 59", output.result)


# ── Play from here ───────────────────────────────────────────────


class FakeRoonConnectionForPlayFromHere:
    """Tracks play_from_here calls with queue reference resolution."""

    def __init__(self, ref_map=None):
        self.play_from_here_calls = []
        self._ref_map = ref_map
        self.api = MagicMock()
        self.api.zones = {
            "z1": {
                "display_name": "Living Room",
                "zone_id": "z1",
                "outputs": [{"output_id": "o1", "display_name": "Living Room"}],
            },
        }
        self.api.outputs = {
            "o1": {"output_id": "o1", "display_name": "Living Room", "zone_id": "z1"},
        }

    def play_from_here(self, queue_item_id, zone=None):
        self.play_from_here_calls.append({"queue_item_id": queue_item_id, "zone": zone})

    def resolve_queue_ref(self, hex_ref, zone=None):
        if self._ref_map:
            qid, err = self._ref_map.resolve(hex_ref)
            if qid is not None:
                return qid
            if err:
                raise ValueError(err)
        raise ValueError(f"Queue reference '{hex_ref}' not found.")


class TestPlayFromHereAction(unittest.TestCase):

    def _run(self, queue_item_id=None, queue_ref=None, zone=None, ref_map=None):
        from tools.roon_action import (
            RoonActionTool,
            RoonActionToolConfig,
            RoonActionToolInputSchema,
        )

        fake = FakeRoonConnectionForPlayFromHere(ref_map=ref_map)
        tool = RoonActionTool(config=RoonActionToolConfig(resolve_zone=lambda z: z))
        tool.roon_connection = fake
        params = RoonActionToolInputSchema(
            action="play_from_here",
            queue_item_id=queue_item_id,
            queue_ref=queue_ref,
            zone=zone,
        )
        output = asyncio.run(tool.run_async(params))
        return output, fake

    def test_play_from_here_dispatches_with_queue_item_id(self):
        output, fake = self._run(queue_item_id=83581, zone="Living Room")
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.play_from_here_calls[0]["queue_item_id"], 83581)

    def test_play_from_here_requires_id_or_ref(self):
        from tools.roon_action import RoonActionToolInputSchema

        with self.assertRaises(ValueError):
            RoonActionToolInputSchema(action="play_from_here")

    def test_play_from_here_passes_zone(self):
        output, fake = self._run(queue_item_id=83536, zone="Living Room")
        self.assertEqual(fake.play_from_here_calls[0]["zone"], "Living Room")

# ── Play from here on playback mixin ─────────────────────────────


class TestPlayFromHereMethod(unittest.TestCase):
    """Test the play_from_here method on the playback mixin."""

    def _make_host(self, queue_items=None):
        from roon_core.playback import RoonPlaybackMixin

        items = queue_items if queue_items is not None else SAMPLE_QUEUE_ITEMS

        class FakeHost(RoonPlaybackMixin):
            def __init__(self):
                self.api = MagicMock()
                self.api.zones = {
                    "z1": {"display_name": "Living Room", "zone_id": "z1"},
                }
                self.target_zone = "Living Room"
                self._queue_items = items

            def _lookup_output_id(self, zone=None):
                return "z1"

            def get_queue_items(self, zone=None):
                return self._queue_items

        return FakeHost()

    def test_play_from_here_calls_api_request(self):
        host = self._make_host()
        host.play_from_here(queue_item_id=83581, zone="Living Room")
        host.api._request.assert_called_once_with(
            "com.roonlabs.transport:2/play_from_here",
            {"zone_or_output_id": "z1", "queue_item_id": 83581},
        )

    def test_play_from_here_rejects_invalid_id(self):
        host = self._make_host()
        with self.assertRaises(ValueError) as ctx:
            host.play_from_here(queue_item_id=19, zone="Living Room")
        self.assertIn("NOT the track position number", str(ctx.exception))

    def test_play_from_here_allows_valid_id(self):
        host = self._make_host()
        # 83536 is in SAMPLE_QUEUE_ITEMS
        host.play_from_here(queue_item_id=83536, zone="Living Room")
        host.api._request.assert_called_once()

    def test_play_from_here_skips_validation_when_no_queue_data(self):
        host = self._make_host(queue_items=[])
        # No queue data — can't validate, so allow it through
        host.play_from_here(queue_item_id=99999, zone="Living Room")
        host.api._request.assert_called_once()


# ── Library actions ────────────────────────────────────────────────


class TestAutoRadio(unittest.TestCase):
    """Test set_auto_radio playback method and action."""

    def test_set_auto_radio_calls_api(self):
        from roon_core.playback import RoonPlaybackMixin

        class FakeHost(RoonPlaybackMixin):
            def __init__(self):
                self.api = MagicMock()
                self.target_zone = "Living Room"

            def _lookup_output_id(self, zone=None):
                return "z1"

        host = FakeHost()
        host.set_auto_radio(auto_radio=True, zone="Living Room")
        host.api._request.assert_called_once_with(
            "com.roonlabs.transport:2/change_settings",
            {"zone_or_output_id": "z1", "auto_radio": True},
        )

    def test_set_auto_radio_requires_auto_radio_field(self):
        from tools.roon_action import RoonActionToolInputSchema

        with self.assertRaises(ValueError):
            RoonActionToolInputSchema(action="set_auto_radio", zone="Living Room")


class TestMuteAll(unittest.TestCase):
    """Test mute_all and unmute_all."""

    def test_mute_all_calls_api(self):
        from roon_core.playback import RoonPlaybackMixin

        class FakeHost(RoonPlaybackMixin):
            def __init__(self):
                self.api = MagicMock()

        host = FakeHost()
        host.mute_all()
        host.api._request.assert_called_once_with(
            "com.roonlabs.transport:2/mute_all",
            {"how": "mute"},
        )

    def test_unmute_all_calls_api(self):
        from roon_core.playback import RoonPlaybackMixin

        class FakeHost(RoonPlaybackMixin):
            def __init__(self):
                self.api = MagicMock()

        host = FakeHost()
        host.unmute_all()
        host.api._request.assert_called_once_with(
            "com.roonlabs.transport:2/mute_all",
            {"how": "unmute"},
        )

class TestSourceControlTargeting(unittest.TestCase):
    """Test control_key passthrough for standby/convenience_switch."""

    def test_standby_passes_control_key(self):
        from roon_core.playback import RoonPlaybackMixin

        class FakeHost(RoonPlaybackMixin):
            def __init__(self):
                self.api = MagicMock()

            def _lookup_output_id_for_controls(self, zone=None, output=None):
                return "o1"

        host = FakeHost()
        host.standby(zone="Living Room", control_key="source-1")
        host.api.standby.assert_called_once_with(output_id="o1", control_key="source-1")

    def test_convenience_switch_passes_control_key(self):
        from roon_core.playback import RoonPlaybackMixin

        class FakeHost(RoonPlaybackMixin):
            def __init__(self):
                self.api = MagicMock()

            def _lookup_output_id_for_controls(self, zone=None, output=None):
                return "o1"

        host = FakeHost()
        host.convenience_switch(zone="Living Room", control_key="source-1")
        host.api.convenience_switch.assert_called_once_with(output_id="o1", control_key="source-1")


if __name__ == "__main__":
    unittest.main()
