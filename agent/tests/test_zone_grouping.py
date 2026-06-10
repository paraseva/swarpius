"""Unit tests for zone grouping: name resolution fix and group query methods.

These tests use mocked zone/output data — no live Roon Core required.
"""

import unittest
from unittest.mock import MagicMock

from app.exceptions import ZoneLookupError
from roon_core.connection import RoonConnection


def _make_api(zones: dict, outputs: dict) -> MagicMock:
    """Create a mock roonapi with the given zone and output data."""
    api = MagicMock()
    api.zones = zones
    api.outputs = outputs
    api.group_outputs = MagicMock()
    api.ungroup_outputs = MagicMock()
    return api


# ── Test data: 4 outputs, 2 grouped ──────────────────────────

GROUPED_ZONES = {
    "zone_abc": {
        "zone_id": "zone_abc",
        "display_name": "MDAC+ USB + 1",
        "state": "playing",
        "outputs": [
            {"output_id": "out_1", "display_name": "MDAC+ USB", "zone_id": "zone_abc"},
            {"output_id": "out_2", "display_name": "BT-W5 Akash", "zone_id": "zone_abc"},
        ],
    },
    "zone_def": {
        "zone_id": "zone_def",
        "display_name": "Chord Qutest",
        "state": "stopped",
        "outputs": [
            {"output_id": "out_3", "display_name": "Chord Qutest", "zone_id": "zone_def"},
        ],
    },
}

GROUPED_OUTPUTS = {
    "out_1": {"output_id": "out_1", "display_name": "MDAC+ USB", "zone_id": "zone_abc"},
    "out_2": {"output_id": "out_2", "display_name": "BT-W5 Akash", "zone_id": "zone_abc"},
    "out_3": {"output_id": "out_3", "display_name": "Chord Qutest", "zone_id": "zone_def"},
}

# ── Test data: all ungrouped ──────────────────────────────────

UNGROUPED_ZONES = {
    "zone_1": {
        "zone_id": "zone_1",
        "display_name": "MDAC+ USB",
        "state": "stopped",
        "outputs": [
            {"output_id": "out_1", "display_name": "MDAC+ USB", "zone_id": "zone_1"},
        ],
    },
    "zone_2": {
        "zone_id": "zone_2",
        "display_name": "BT-W5 Akash",
        "state": "stopped",
        "outputs": [
            {"output_id": "out_2", "display_name": "BT-W5 Akash", "zone_id": "zone_2"},
        ],
    },
    "zone_3": {
        "zone_id": "zone_3",
        "display_name": "Chord Qutest",
        "state": "stopped",
        "outputs": [
            {"output_id": "out_3", "display_name": "Chord Qutest", "zone_id": "zone_3"},
        ],
    },
}

UNGROUPED_OUTPUTS = {
    "out_1": {"output_id": "out_1", "display_name": "MDAC+ USB", "zone_id": "zone_1"},
    "out_2": {"output_id": "out_2", "display_name": "BT-W5 Akash", "zone_id": "zone_2"},
    "out_3": {"output_id": "out_3", "display_name": "Chord Qutest", "zone_id": "zone_3"},
}


def _make_connection(zones, outputs):
    """Create a RoonConnection with mocked internals."""
    conn = object.__new__(RoonConnection)
    conn.api = _make_api(zones, outputs)
    conn._preferred_output_id = None
    return conn


class TestGroupUngroupResolution(unittest.TestCase):
    """Test that group_zones/ungroup_zones resolve output names correctly."""

    def test_group_zones_resolves_ungrouped_output_names(self):
        """Grouping ungrouped zones by their output display names should work."""
        conn = _make_connection(UNGROUPED_ZONES, UNGROUPED_OUTPUTS)
        conn.group_zones(["MDAC+ USB", "BT-W5 Akash"])
        conn.api.group_outputs.assert_called_once()
        output_ids = conn.api.group_outputs.call_args[1]["output_ids"]
        self.assertEqual(set(output_ids), {"out_1", "out_2"})

    def test_ungroup_zones_resolves_grouped_output_names(self):
        """Ungrouping by output display names should work even when the
        outputs are part of a group (zone display name is 'X + N')."""
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        # "MDAC+ USB" is an output inside zone "MDAC+ USB + 1"
        # The old code would fail here because it looks for a zone named "MDAC+ USB"
        conn.ungroup_zones(["MDAC+ USB", "BT-W5 Akash"])
        conn.api.ungroup_outputs.assert_called_once()
        output_ids = conn.api.ungroup_outputs.call_args[1]["output_ids"]
        self.assertEqual(set(output_ids), {"out_1", "out_2"})

    def test_ungroup_single_output_from_group(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        conn.ungroup_zones(["BT-W5 Akash"])
        conn.api.ungroup_outputs.assert_called_once()
        output_ids = conn.api.ungroup_outputs.call_args[1]["output_ids"]
        self.assertEqual(output_ids, ["out_2"])

    def test_group_zones_rejects_fewer_than_two(self):
        conn = _make_connection(UNGROUPED_ZONES, UNGROUPED_OUTPUTS)
        with self.assertRaises(ValueError):
            conn.group_zones(["MDAC+ USB"])

    def test_group_zones_unknown_output_raises(self):
        conn = _make_connection(UNGROUPED_ZONES, UNGROUPED_OUTPUTS)
        with self.assertRaises(ZoneLookupError):
            conn.group_zones(["MDAC+ USB", "Nonexistent"])


class TestLookupOutputIdGroupFallback(unittest.TestCase):
    """Test that _lookup_output_id falls back to output names in groups."""

    def test_lookup_by_zone_display_name(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        zone_id = conn._lookup_output_id("MDAC+ USB + 1")
        self.assertEqual(zone_id, "zone_abc")

    def test_lookup_by_output_name_in_group(self):
        """When the zone name matches an output inside a group, return
        the output_id — not an error."""
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        output_id = conn._lookup_output_id("MDAC+ USB")
        self.assertEqual(output_id, "out_1")

    def test_lookup_by_second_output_name_in_group(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        output_id = conn._lookup_output_id("BT-W5 Akash")
        self.assertEqual(output_id, "out_2")

    def test_lookup_unknown_zone_raises(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        with self.assertRaises(ZoneLookupError):
            conn._lookup_output_id("Nonexistent")


class TestGroupQueryMethods(unittest.TestCase):
    """Test group state query methods on RoonZoneMixin."""

    def test_is_zone_grouped_true(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        self.assertTrue(conn.is_zone_grouped("MDAC+ USB + 1"))

    def test_is_zone_grouped_false(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        self.assertFalse(conn.is_zone_grouped("Chord Qutest"))

    def test_is_zone_grouped_unknown_raises(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        with self.assertRaises(ZoneLookupError):
            conn.is_zone_grouped("Nonexistent")

    def test_get_grouped_output_names(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        names = conn.get_grouped_output_names("MDAC+ USB + 1")
        self.assertEqual(names, ["MDAC+ USB", "BT-W5 Akash"])

    def test_get_grouped_output_names_ungrouped_returns_single(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        names = conn.get_grouped_output_names("Chord Qutest")
        self.assertEqual(names, ["Chord Qutest"])

    def test_get_zones_with_group_info(self):
        conn = _make_connection(GROUPED_ZONES, GROUPED_OUTPUTS)
        zones = conn.get_zones_with_group_info()
        self.assertEqual(len(zones), 2)

        grouped = next(z for z in zones if z["is_grouped"])
        self.assertEqual(grouped["display_name"], "MDAC+ USB + 1")
        self.assertEqual(grouped["group_members"], ["MDAC+ USB", "BT-W5 Akash"])
        self.assertEqual(grouped["state"], "playing")

        ungrouped = next(z for z in zones if not z["is_grouped"])
        self.assertEqual(ungrouped["display_name"], "Chord Qutest")
        self.assertEqual(ungrouped["group_members"], ["Chord Qutest"])

    def test_get_zones_with_group_info_filters_empty_outputs(self):
        """Ghost zones with empty outputs (after ungrouping) should be filtered."""
        zones_with_ghost = {
            **GROUPED_ZONES,
            "zone_ghost": {
                "zone_id": "zone_ghost",
                "display_name": "Unnamed",
                "state": "stopped",
                "outputs": [],
            },
        }
        conn = _make_connection(zones_with_ghost, GROUPED_OUTPUTS)
        zones = conn.get_zones_with_group_info()
        names = [z["display_name"] for z in zones]
        self.assertNotIn("Unnamed", names)
        self.assertEqual(len(zones), 2)


if __name__ == "__main__":
    unittest.main()
