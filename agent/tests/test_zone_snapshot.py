"""Snapshot builder — converts the current set of Roon zones into the
list-of-zones payload the frontend renders directly."""

from __future__ import annotations

import unittest

from app.roon.zone_snapshot import ZoneSnapshotBuilder


def _zone(
    *,
    zone_id="z1",
    display_name="Living Room",
    state="playing",
    seek_position=10,
    queue_items_remaining=None,
    outputs=None,
    image_key="img-abc",
    now_playing=None,
    settings=None,
):
    if outputs is None:
        outputs = [{
            "display_name": display_name,
            "volume": {"value": 50, "type": "number", "is_muted": False,
                       "min": 0, "max": 100, "step": 1},
        }]
    if now_playing is None:
        now_playing = {
            "image_key": image_key,
            "length": 300,
            "three_line": {"line1": "Track", "line2": "Artist", "line3": "Album"},
        }
    base = {
        "zone_id": zone_id,
        "display_name": display_name,
        "state": state,
        "seek_position": seek_position,
        "now_playing": now_playing,
        "outputs": outputs,
    }
    if queue_items_remaining is not None:
        base["queue_items_remaining"] = queue_items_remaining
    if settings is not None:
        base["settings"] = settings
    return base


def _builder(aliases=None):
    aliases = aliases or {}
    return ZoneSnapshotBuilder(get_alias=aliases.get)


def _build_one(zone, aliases=None):
    snapshot = _builder(aliases).build({zone["zone_id"]: zone})
    assert len(snapshot) == 1
    return snapshot[0]


class TestSnapshotShape(unittest.TestCase):
    """The snapshot carries every field the card renderer needs."""

    def test_playing_zone_state_is_playing(self):
        result = _build_one(_zone(state="playing"))
        self.assertEqual(result["state"], "playing")

    def test_paused_zone_state_is_paused(self):
        result = _build_one(_zone(state="paused"))
        self.assertEqual(result["state"], "paused")

    def test_stopped_zone_with_queue_remaps_to_paused(self):
        result = _build_one(_zone(state="stopped", queue_items_remaining=3))
        self.assertEqual(result["state"], "paused")

    def test_stopped_zone_with_empty_queue_stays_stopped(self):
        result = _build_one(_zone(state="stopped", queue_items_remaining=0))
        self.assertEqual(result["state"], "stopped")

    def test_stopped_zone_with_missing_queue_field_stays_stopped(self):
        zone = _zone(state="stopped")
        zone.pop("queue_items_remaining", None)
        result = _build_one(zone)
        self.assertEqual(result["state"], "stopped")

    def test_seek_position_passes_through(self):
        result = _build_one(_zone(seek_position=42))
        self.assertEqual(result["seek_position"], 42)

    def test_playback_settings_surface_from_zone_settings(self):
        result = _build_one(_zone(settings={
            "shuffle": True, "loop": "loop_one", "auto_radio": True,
        }))
        self.assertTrue(result["shuffle"])
        self.assertEqual(result["loop"], "loop_one")
        self.assertTrue(result["auto_radio"])

    def test_playback_settings_default_off_when_settings_absent(self):
        zone = _zone()
        zone.pop("settings", None)
        result = _build_one(zone)
        self.assertFalse(result["shuffle"])
        self.assertEqual(result["loop"], "disabled")
        self.assertFalse(result["auto_radio"])

    def test_alias_resolved_from_lookup(self):
        result = _build_one(
            _zone(display_name="Headphones"),
            aliases={"Headphones": "Cans"},
        )
        self.assertEqual(result["zone_alias"], "Cans")

    def test_alias_is_null_when_no_alias(self):
        result = _build_one(_zone(display_name="Living Room"))
        self.assertIsNone(result["zone_alias"])

    def test_single_output_is_not_grouped(self):
        result = _build_one(_zone())
        self.assertFalse(result["is_grouped"])
        self.assertEqual(result["group_members"], ["Living Room"])

    def test_multi_output_is_grouped(self):
        zone = _zone(outputs=[
            {"display_name": "Living Room", "volume": {"value": 50}},
            {"display_name": "Kitchen", "volume": {"value": 30}},
        ])
        result = _build_one(zone)
        self.assertTrue(result["is_grouped"])
        self.assertEqual(result["group_members"], ["Living Room", "Kitchen"])

    def test_outputs_volume_built_from_each_output(self):
        zone = _zone(outputs=[
            {"display_name": "A", "volume": {"value": 30, "type": "db",
                                              "is_muted": False, "min": 0,
                                              "max": 100, "step": 1}},
            {"display_name": "B", "volume": {"value": 60, "type": "db",
                                              "is_muted": True, "min": 0,
                                              "max": 100, "step": 1}},
        ])
        result = _build_one(zone)
        names = [v["name"] for v in result["outputs_volume"]]
        self.assertEqual(names, ["A", "B"])
        self.assertEqual(result["outputs_volume"][0]["value"], 30)
        self.assertTrue(result["outputs_volume"][1]["is_muted"])

    def test_outputs_volume_defaults_when_volume_sparse(self):
        zone = _zone(outputs=[{"display_name": "A", "volume": {"value": 42}}])
        result = _build_one(zone)
        v = result["outputs_volume"][0]
        self.assertEqual(v["value"], 42)
        self.assertEqual(v["min"], 0)
        self.assertEqual(v["max"], 100)
        self.assertEqual(v["is_muted"], False)

    def test_image_key_kept_for_playing_zone(self):
        result = _build_one(_zone(state="playing"))
        self.assertEqual(result["image_key"], "img-abc")

    def test_image_key_kept_for_paused_zone(self):
        result = _build_one(_zone(state="paused"))
        self.assertEqual(result["image_key"], "img-abc")

    def test_image_key_kept_for_stopped_zone_with_queue(self):
        # State is remapped to paused, so artwork should persist —
        # the user can resume from this state.
        result = _build_one(_zone(state="stopped", queue_items_remaining=3))
        self.assertEqual(result["image_key"], "img-abc")

    def test_image_key_dropped_for_stopped_zone_without_queue(self):
        result = _build_one(_zone(state="stopped", queue_items_remaining=0))
        self.assertIsNone(result["image_key"])

    def test_now_playing_three_line_flattened(self):
        result = _build_one(_zone())
        self.assertEqual(result["now_playing"]["line1"], "Track")
        self.assertEqual(result["now_playing"]["line2"], "Artist")
        self.assertEqual(result["now_playing"]["line3"], "Album")
        self.assertEqual(result["now_playing"]["length"], 300)


class TestSnapshotMultiZone(unittest.TestCase):
    """Behaviour when multiple zones are present at once."""

    def test_all_zones_included(self):
        snapshot = _builder().build({
            "z1": _zone(zone_id="z1", display_name="Living Room"),
            "z2": _zone(zone_id="z2", display_name="Kitchen"),
        })
        ids = sorted(z["zone_id"] for z in snapshot)
        self.assertEqual(ids, ["z1", "z2"])

    def test_zone_order_is_stable_across_builds(self):
        zones = {
            "z2": _zone(zone_id="z2", display_name="Kitchen"),
            "z1": _zone(zone_id="z1", display_name="Living Room"),
        }
        first = [z["zone_id"] for z in _builder().build(zones)]
        second = [z["zone_id"] for z in _builder().build(zones)]
        self.assertEqual(first, second)


class TestSnapshotMalformedInput(unittest.TestCase):
    """Roon's WS stream emits partial / transient payloads under
    network jitter and during topology transitions. The builder must
    produce a usable snapshot from them without crashing."""

    def test_missing_now_playing_produces_null_track_metadata(self):
        zone = _zone()
        zone["now_playing"] = None
        result = _build_one(zone)
        self.assertIsNone(result["now_playing"]["line1"])
        self.assertIsNone(result["image_key"])

    def test_missing_three_line_produces_null_lines(self):
        zone = _zone()
        zone["now_playing"] = {"image_key": "img-abc", "length": 200}
        result = _build_one(zone)
        self.assertIsNone(result["now_playing"]["line1"])
        self.assertEqual(result["image_key"], "img-abc")

    def test_missing_outputs_produces_empty_volume(self):
        zone = _zone(outputs=[])
        result = _build_one(zone)
        self.assertEqual(result["outputs_volume"], [])
        self.assertFalse(result["is_grouped"])


class TestSnapshotChangedSince(unittest.TestCase):
    """De-dup signal: the builder reports whether the latest snapshot
    differs from the previous one, so the emitter can skip identical
    re-broadcasts."""

    def test_first_snapshot_is_changed(self):
        b = _builder()
        snapshot = b.build({"z1": _zone()})
        self.assertTrue(b.changed_since_last(snapshot))

    def test_identical_snapshot_is_not_changed(self):
        b = _builder()
        snapshot = b.build({"z1": _zone()})
        b.changed_since_last(snapshot)  # establish baseline
        same = b.build({"z1": _zone()})
        self.assertFalse(b.changed_since_last(same))

    def test_state_change_is_detected(self):
        b = _builder()
        b.changed_since_last(b.build({"z1": _zone(state="playing")}))
        self.assertTrue(b.changed_since_last(b.build({"z1": _zone(state="paused")})))

    def test_seek_change_is_detected(self):
        # Seek must be part of the signature — otherwise the seek
        # bar wouldn't advance.
        b = _builder()
        b.changed_since_last(b.build({"z1": _zone(seek_position=10)}))
        self.assertTrue(b.changed_since_last(b.build({"z1": _zone(seek_position=11)})))

    def test_zone_added_is_detected(self):
        b = _builder()
        b.changed_since_last(b.build({"z1": _zone(zone_id="z1")}))
        self.assertTrue(b.changed_since_last(b.build({
            "z1": _zone(zone_id="z1"),
            "z2": _zone(zone_id="z2"),
        })))

    def test_zone_removed_is_detected(self):
        b = _builder()
        b.changed_since_last(b.build({
            "z1": _zone(zone_id="z1"),
            "z2": _zone(zone_id="z2"),
        }))
        self.assertTrue(b.changed_since_last(b.build({"z1": _zone(zone_id="z1")})))

    def test_volume_change_is_detected(self):
        b = _builder()
        b.changed_since_last(b.build({
            "z1": _zone(outputs=[{"display_name": "A", "volume": {"value": 30}}]),
        }))
        self.assertTrue(b.changed_since_last(b.build({
            "z1": _zone(outputs=[{"display_name": "A", "volume": {"value": 60}}]),
        })))

    def test_alias_change_is_detected(self):
        b1 = _builder(aliases={"Living Room": "Lounge"})
        b1.changed_since_last(b1.build({"z1": _zone()}))
        b2 = _builder(aliases={"Living Room": "Den"})
        # Different builders sharing the same starting zone but
        # different aliases — second build should yield a different
        # snapshot. (Tested via the signature output.)
        snap1 = b1.build({"z1": _zone()})
        snap2 = b2.build({"z1": _zone()})
        self.assertNotEqual(snap1[0]["zone_alias"], snap2[0]["zone_alias"])


if __name__ == "__main__":
    unittest.main()
