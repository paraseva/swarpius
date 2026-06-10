"""Live Roon tests to inspect and manipulate zone grouping.

Requires a running Roon Core with at least 3 zones available.
Before running, group exactly 2 zones in the Roon desktop app so the
inspection tests can observe existing group state.

Run with:
    .venv-wsl/bin/python -m pytest tests/test_zone_grouping_live.py -v -s -m live_roon

The -s flag is important — it shows the printed output.

Tests are ordered: inspection first, then manipulation. The manipulation
tests restore original state after each test.
"""

import json
import time

import pytest

from tests.conftest import get_live_roon

pytestmark = pytest.mark.live_roon

SETTLE_TIME = 2.0  # seconds to wait for Roon Core to propagate state changes


def _group_by_output_names(roon, output_names):
    """Group outputs by their display names (not zone names).

    The existing roon.group_zones() uses _lookup_output_id_for_controls
    which resolves via zone display names — that fails for outputs that
    are already grouped (their zone name changes to 'X + N'). This
    helper resolves via output display names directly.
    """
    output_ids = []
    for name in output_names:
        for oid, o in roon.api.outputs.items():
            if o.get("display_name", "").lower() == name.lower():
                output_ids.append(oid)
                break
        else:
            raise ValueError(f"Output '{name}' not found")
    roon.api.group_outputs(output_ids)


def _ungroup_by_output_names(roon, output_names):
    """Ungroup outputs by their display names (not zone names)."""
    output_ids = []
    for name in output_names:
        for oid, o in roon.api.outputs.items():
            if o.get("display_name", "").lower() == name.lower():
                output_ids.append(oid)
                break
        else:
            raise ValueError(f"Output '{name}' not found")
    roon.api.ungroup_outputs(output_ids)


def _dump_zones(roon, label=""):
    """Print current zone state. Returns the zones dict for assertions."""
    zones = roon.api.zones
    if label:
        print(f"\n  [{label}]")
    for zone_id, zone in zones.items():
        outputs = zone.get("outputs", [])
        is_grouped = len(outputs) > 1
        output_names = [o.get("display_name") for o in outputs]
        state = zone.get("state", "?")
        marker = " (GROUPED)" if is_grouped else ""
        print(f"    {zone.get('display_name')} [{state}]{marker} — outputs: {output_names}")
    return zones


def _get_all_output_ids(roon):
    """Return dict of output_name -> output_id."""
    return {
        o.get("display_name"): oid
        for oid, o in roon.api.outputs.items()
    }


def _wait():
    """Wait for Roon Core to settle after a group change."""
    time.sleep(SETTLE_TIME)


# ─────────────────────────────────────────────────────────────
# Part 1: Inspection (non-destructive)
# ─────────────────────────────────────────────────────────────

class TestZoneGroupingInspection:
    """Dump zone and output state to understand grouping behaviour."""

    @classmethod
    def setup_class(cls):
        cls.roon = get_live_roon()

    def test_dump_all_zones(self):
        """Print every zone with full structure including outputs."""
        zones = self.roon.api.zones
        print(f"\n{'='*60}")
        print(f"ZONES ({len(zones)} total)")
        print(f"{'='*60}")
        for zone_id, zone in zones.items():
            outputs = zone.get("outputs", [])
            is_grouped = len(outputs) > 1
            print(f"\n--- Zone: {zone.get('display_name')} ---")
            print(f"  zone_id:    {zone_id}")
            print(f"  state:      {zone.get('state')}")
            print(f"  is_grouped: {is_grouped}")
            print(f"  outputs:    {len(outputs)}")
            for i, output in enumerate(outputs):
                main = " (MAIN)" if i == 0 and is_grouped else ""
                can_group = output.get("can_group_with_output_ids", [])
                print(f"    [{i}] {output.get('display_name')}{main}")
                print(f"        output_id:    {output.get('output_id')}")
                print(f"        zone_id:      {output.get('zone_id')}")
                print(f"        can_group_with: {len(can_group)} outputs")
            now_playing = zone.get("now_playing")
            if now_playing:
                two_line = now_playing.get("two_line", {})
                print(f"  now_playing: {two_line.get('line1', '?')} - {two_line.get('line2', '?')}")
            else:
                print("  now_playing: (none)")
        print()

    def test_dump_all_outputs(self):
        """Print every output to see which zone it belongs to."""
        outputs = self.roon.api.outputs
        print(f"\n{'='*60}")
        print(f"OUTPUTS ({len(outputs)} total)")
        print(f"{'='*60}")
        for output_id, output in outputs.items():
            zone_id = output.get("zone_id")
            zone = self.roon.api.zones.get(zone_id, {})
            zone_name = zone.get("display_name", "???")
            zone_output_count = len(zone.get("outputs", []))
            print(f"\n  {output.get('display_name')}")
            print(f"    output_id:      {output_id}")
            print(f"    zone_id:        {zone_id}")
            print(f"    parent_zone:    {zone_name}")
            print(f"    zone_outputs:   {zone_output_count}")
            print(f"    is_grouped:     {zone_output_count > 1}")
            volume = output.get("volume", {})
            if volume:
                print(f"    volume_type:    {volume.get('type')}")
                print(f"    volume_value:   {volume.get('value')}")
        print()

    def test_roonapi_grouping_helpers(self):
        """Test the roonapi helper methods for grouping detection."""
        outputs = self.roon.api.outputs
        print(f"\n{'='*60}")
        print("ROONAPI GROUPING HELPERS")
        print(f"{'='*60}")
        for output_id in outputs:
            name = outputs[output_id].get("display_name")
            is_grouped = self.roon.api.is_grouped(output_id)
            is_main = self.roon.api.is_group_main(output_id) if is_grouped else None
            members = self.roon.api.grouped_zone_names(output_id) if is_grouped else []
            print(f"\n  {name}")
            print(f"    is_grouped:  {is_grouped}")
            if is_grouped:
                print(f"    is_main:     {is_main}")
                print(f"    members:     {members}")
        print()

    def test_dump_zone_names_and_ids(self):
        """Quick summary: zone display names, and our existing helper methods."""
        print(f"\n{'='*60}")
        print("SWARPIUS ZONE HELPERS")
        print(f"{'='*60}")
        print(f"\n  get_zone_names():     {self.roon.get_zone_names()}")
        print(f"  get_default_zone():   {self.roon.get_default_zone()}")

        snapshot = self.roon.get_zones_snapshot()
        print(f"\n  get_zones_snapshot() ({len(snapshot)} zones):")
        for z in snapshot:
            outputs = z.get("outputs", [])
            print(f"    {z.get('display_name')} [{z.get('state')}] — {len(outputs)} output(s)")
            for o in outputs:
                print(f"      - {o.get('display_name')}")
        print()

    def test_raw_zone_json(self):
        """Dump full raw JSON of one grouped zone for detailed inspection."""
        zones = self.roon.api.zones
        grouped = [z for z in zones.values() if len(z.get("outputs", [])) > 1]
        if not grouped:
            print("\n  No grouped zones found — skip raw dump")
            return
        zone = grouped[0]
        print(f"\n{'='*60}")
        print(f"RAW JSON: {zone.get('display_name')}")
        print(f"{'='*60}")
        interesting_keys = [
            "zone_id", "display_name", "state", "outputs",
            "is_next_allowed", "is_previous_allowed",
            "is_pause_allowed", "is_play_allowed", "is_seek_allowed",
            "queue_items_remaining", "queue_time_remaining",
            "settings",
        ]
        filtered = {k: zone[k] for k in interesting_keys if k in zone}
        print(json.dumps(filtered, indent=2, default=str))
        print()


# ─────────────────────────────────────────────────────────────
# Part 2: Manipulation (changes groups, then restores)
#
# IMPORTANT: These tests change the group state on your Roon
# Core. They attempt to restore the original state after each
# test, but if a test is interrupted you may need to manually
# re-group in the Roon desktop app.
# ─────────────────────────────────────────────────────────────

class TestZoneGroupingManipulation:
    """Test grouping/ungrouping operations to understand edge cases."""

    @classmethod
    def setup_class(cls):
        cls.roon = get_live_roon()
        # Capture initial group state so we can restore
        cls.initial_grouped = []
        for zone in cls.roon.api.zones.values():
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                cls.initial_grouped.append(
                    [o.get("display_name") for o in outputs]
                )
        print(f"\n  Initial groups: {cls.initial_grouped}")
        if not cls.initial_grouped:
            pytest.skip("No grouped zones found — group at least 2 zones in Roon before running")

    def _restore_initial_groups(self):
        """Best-effort restore of the initial group state."""
        # First ungroup everything
        for zone in list(self.roon.api.zones.values()):
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                names = [o.get("display_name") for o in outputs]
                print(f"    Restoring: ungrouping {names}")
                try:
                    _ungroup_by_output_names(self.roon, names)
                    _wait()
                except Exception as e:
                    print(f"    Restore ungroup failed: {e}")

        # Then re-group what was originally grouped
        for group_names in self.initial_grouped:
            print(f"    Restoring: re-grouping {group_names}")
            try:
                _group_by_output_names(self.roon, group_names)
                _wait()
            except Exception as e:
                print(f"    Restore group failed: {e}")

    def test_01_ungroup_and_regroup(self):
        """Ungroup the existing group, observe state, then re-group.

        Answers: What happens to zone_ids and display_names after ungrouping?
        """
        print(f"\n{'='*60}")
        print("TEST: Ungroup and regroup")
        print(f"{'='*60}")

        group_names = self.initial_grouped[0]
        print(f"\n  Will ungroup: {group_names}")

        _dump_zones(self.roon, "BEFORE ungroup")

        # Ungroup
        _ungroup_by_output_names(self.roon, group_names)
        _wait()

        _dump_zones(self.roon, "AFTER ungroup")

        # Check that each output is now its own zone
        zone_names = self.roon.get_zone_names()
        print(f"\n  Zone names after ungroup: {zone_names}")
        for name in group_names:
            assert name in zone_names, f"Expected '{name}' to be an independent zone after ungrouping"

        # Re-group
        print(f"\n  Re-grouping: {group_names}")
        _group_by_output_names(self.roon, group_names)
        _wait()

        _dump_zones(self.roon, "AFTER re-group")

        # Verify group is back
        found_group = False
        for zone in self.roon.api.zones.values():
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                found_group = True
                member_names = [o.get("display_name") for o in outputs]
                print(f"  Restored group: {zone.get('display_name')} — members: {member_names}")
        assert found_group, "Expected at least one grouped zone after re-grouping"

    def test_02_group_three_zones(self):
        """Group 3 zones together (if available).

        Answers: Does Roon support 3+ zone groups? What does the display_name become?
        """
        print(f"\n{'='*60}")
        print("TEST: Group three zones")
        print(f"{'='*60}")

        # We need at least 3 outputs total
        all_output_names = list(_get_all_output_ids(self.roon).keys())
        if len(all_output_names) < 3:
            print(f"  Only {len(all_output_names)} outputs available, need 3 — skipping")
            pytest.skip("Need at least 3 outputs for this test")

        # First, ungroup everything so we start clean
        for zone in list(self.roon.api.zones.values()):
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                _ungroup_by_output_names(self.roon, [o.get("display_name") for o in outputs])
                _wait()

        _dump_zones(self.roon, "BEFORE 3-way group (all ungrouped)")

        # Group all 3
        three_names = all_output_names[:3]
        print(f"\n  Grouping 3 zones: {three_names}")
        _group_by_output_names(self.roon, three_names)
        _wait()

        _dump_zones(self.roon, "AFTER 3-way group")

        # Inspect the result
        for zone in self.roon.api.zones.values():
            outputs = zone.get("outputs", [])
            if len(outputs) >= 3:
                print(f"\n  3-way group display_name: '{zone.get('display_name')}'")
                print(f"  Output count: {len(outputs)}")
                for i, o in enumerate(outputs):
                    print(f"    [{i}] {o.get('display_name')}")

        # Restore
        self._restore_initial_groups()
        _dump_zones(self.roon, "AFTER restore")

    def test_03_partial_ungroup_from_three(self):
        """Group 3, then ungroup just 1. Does the remaining 2 stay grouped?

        Answers: Can you partially ungroup? Or does it break the entire group?
        """
        print(f"\n{'='*60}")
        print("TEST: Partial ungroup from 3-way group")
        print(f"{'='*60}")

        all_output_names = list(_get_all_output_ids(self.roon).keys())
        if len(all_output_names) < 3:
            pytest.skip("Need at least 3 outputs for this test")

        # Start clean
        for zone in list(self.roon.api.zones.values()):
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                _ungroup_by_output_names(self.roon, [o.get("display_name") for o in outputs])
                _wait()

        # Group 3
        three_names = all_output_names[:3]
        print(f"\n  Grouping 3: {three_names}")
        _group_by_output_names(self.roon, three_names)
        _wait()
        _dump_zones(self.roon, "AFTER 3-way group")

        # Ungroup just the third one
        to_remove = [three_names[2]]
        print(f"\n  Ungrouping just: {to_remove}")
        _ungroup_by_output_names(self.roon, to_remove)
        _wait()
        _dump_zones(self.roon, "AFTER partial ungroup")

        # Check: are the remaining 2 still grouped?
        remaining_grouped = False
        for zone in self.roon.api.zones.values():
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                remaining_grouped = True
                member_names = [o.get("display_name") for o in outputs]
                print(f"\n  Remaining group: {zone.get('display_name')} — members: {member_names}")

        removed_is_independent = to_remove[0] in self.roon.get_zone_names()
        print(f"  Removed zone '{to_remove[0]}' is independent: {removed_is_independent}")
        print(f"  Remaining 2 still grouped: {remaining_grouped}")

        # Restore
        self._restore_initial_groups()
        _dump_zones(self.roon, "AFTER restore")

    def test_04_regroup_already_grouped(self):
        """Try to group an output that's already in a group with a third zone.

        Answers: Does Roon handle re-grouping automatically, or does it error?
        """
        print(f"\n{'='*60}")
        print("TEST: Group an already-grouped output with a new zone")
        print(f"{'='*60}")

        all_output_names = list(_get_all_output_ids(self.roon).keys())
        if len(all_output_names) < 3:
            pytest.skip("Need at least 3 outputs for this test")

        # Ensure we have a 2-zone group to start with
        for zone in list(self.roon.api.zones.values()):
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                _ungroup_by_output_names(self.roon, [o.get("display_name") for o in outputs])
                _wait()

        pair = all_output_names[:2]
        third = all_output_names[2]

        print(f"\n  First, group pair: {pair}")
        _group_by_output_names(self.roon, pair)
        _wait()
        _dump_zones(self.roon, "AFTER initial pair group")

        # Now try to group the first output (already grouped) with the third
        new_group = [pair[0], third]
        print(f"\n  Now grouping already-grouped '{pair[0]}' with '{third}': {new_group}")
        try:
            _group_by_output_names(self.roon, new_group)
            _wait()
            _dump_zones(self.roon, "AFTER re-group attempt")

            # What happened?
            for zone in self.roon.api.zones.values():
                outputs = zone.get("outputs", [])
                if len(outputs) > 1:
                    member_names = [o.get("display_name") for o in outputs]
                    print(f"\n  Resulting group: {zone.get('display_name')} — members: {member_names}")
                    print(f"  Output count: {len(outputs)}")

            # Is the second output from the original pair still grouped or orphaned?
            orphan = pair[1]
            orphan_is_independent = orphan in self.roon.get_zone_names()
            print(f"\n  Original partner '{orphan}' is now independent: {orphan_is_independent}")

        except Exception as e:
            print(f"\n  Re-group raised exception: {type(e).__name__}: {e}")

        # Restore
        self._restore_initial_groups()
        _dump_zones(self.roon, "AFTER restore")

    def test_05_group_order_matters(self):
        """Group [A, B] then ungroup and group [B, A]. Does the main output change?

        Answers: Is the first output always the main? Does order matter?
        """
        print(f"\n{'='*60}")
        print("TEST: Group order — does first output become main?")
        print(f"{'='*60}")

        # Start clean
        for zone in list(self.roon.api.zones.values()):
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                _ungroup_by_output_names(self.roon, [o.get("display_name") for o in outputs])
                _wait()

        all_output_names = list(_get_all_output_ids(self.roon).keys())
        a, b = all_output_names[0], all_output_names[1]

        # Group [A, B]
        print(f"\n  Grouping [{a}, {b}]")
        _group_by_output_names(self.roon, [a, b])
        _wait()

        for zone in self.roon.api.zones.values():
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                main = outputs[0].get("display_name")
                print(f"  Group [{a}, {b}] → main output: {main}, display_name: {zone.get('display_name')}")

        # Ungroup
        _ungroup_by_output_names(self.roon, [a, b])
        _wait()

        # Group [B, A]
        print(f"\n  Grouping [{b}, {a}]")
        _group_by_output_names(self.roon, [b, a])
        _wait()

        for zone in self.roon.api.zones.values():
            outputs = zone.get("outputs", [])
            if len(outputs) > 1:
                main = outputs[0].get("display_name")
                print(f"  Group [{b}, {a}] → main output: {main}, display_name: {zone.get('display_name')}")

        # Restore
        self._restore_initial_groups()
        _dump_zones(self.roon, "AFTER restore")
