"""Unit tests for zone group naming: storage, config actions, resolution.

Tests group alias CRUD via perform_config_action, and group-aware
zone name resolution. Uses mocked Roon connection with controlled
zone/output data.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.exceptions import ZoneLookupError

try:
    from tests._runtime_fixtures import make_mock_roon_connection
except ModuleNotFoundError:
    from _runtime_fixtures import make_mock_roon_connection

# ── Helpers ───────────────────────────────────────────────────


# Zones: 2 grouped (MDAC + BT-W5), 1 ungrouped (Chord Qutest)
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

# All ungrouped — 3 individual zones
UNGROUPED_ZONES = {
    "zone_1": {
        "zone_id": "zone_1",
        "display_name": "MDAC+ USB",
        "state": "stopped",
        "outputs": [{"output_id": "out_1", "display_name": "MDAC+ USB", "zone_id": "zone_1"}],
    },
    "zone_2": {
        "zone_id": "zone_2",
        "display_name": "BT-W5 Akash",
        "state": "stopped",
        "outputs": [{"output_id": "out_2", "display_name": "BT-W5 Akash", "zone_id": "zone_2"}],
    },
    "zone_3": {
        "zone_id": "zone_3",
        "display_name": "Chord Qutest",
        "state": "stopped",
        "outputs": [{"output_id": "out_3", "display_name": "Chord Qutest", "zone_id": "zone_3"}],
    },
}

UNGROUPED_OUTPUTS = {
    "out_1": {"output_id": "out_1", "display_name": "MDAC+ USB", "zone_id": "zone_1"},
    "out_2": {"output_id": "out_2", "display_name": "BT-W5 Akash", "zone_id": "zone_2"},
    "out_3": {"output_id": "out_3", "display_name": "Chord Qutest", "zone_id": "zone_3"},
}


_temp_dirs: list[tempfile.TemporaryDirectory] = []


def _make_runtime_state(zones=GROUPED_ZONES, outputs=GROUPED_OUTPUTS, target_zone="MDAC+ USB"):
    """Create a RuntimeState with mocked roon connection and temp file paths."""
    from app.runtime.state import RuntimeState
    from tests._runtime_fixtures import wire_config_action, wire_roon_control, wire_zone_domain

    rs = object.__new__(RuntimeState)
    rs.roon_connection = make_mock_roon_connection(zones, outputs, target_zone)
    rs._ws_send_callback = MagicMock()

    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    wire_zone_domain(rs, tmp_path=Path(td.name))

    # Stub broadcast methods on both the runtime *and* the underlying
    # zone_domain so tests asserting ``rs._broadcast_default_zone.assert_called()``
    # see the invocation regardless of whether the caller was a
    # RuntimeState method or a direct ZoneDomain call.
    broadcast_default = MagicMock()
    broadcast_labels = MagicMock()
    rs._broadcast_default_zone = broadcast_default
    rs._broadcast_zone_labels = broadcast_labels
    rs.zone_domain.broadcast_default_zone = broadcast_default

    wire_roon_control(rs)
    wire_config_action(rs)
    return rs


# ── Tests: Config tool actions ────────────────────────────────

class TestGroupConfigActions(unittest.TestCase):
    """Test Group Zones, Ungroup Zones, Get Groups via perform_config_action."""

    def test_group_zones_dispatches_to_connection(self):
        rs = _make_runtime_state()
        result = rs.perform_config_action(
            "Group Zones",
            group_zones=["MDAC+ USB", "BT-W5 Akash"],
        )
        self.assertIn("MDAC+ USB", result)
        rs.roon_connection.group_zones.assert_called_once_with(["MDAC+ USB", "BT-W5 Akash"])

    def test_group_zones_requires_at_least_two(self):
        rs = _make_runtime_state()
        with self.assertRaises(ValueError):
            rs.perform_config_action(
                "Group Zones",
                group_zones=["MDAC+ USB"],
            )

    def test_ungroup_by_zone_name(self):
        rs = _make_runtime_state()
        result = rs.perform_config_action(
            "Ungroup Zones",
            zone="MDAC+ USB + 1",
        )
        self.assertIn("MDAC+ USB", result)
        rs.roon_connection.ungroup_zones.assert_called_once()

    def test_ungroup_by_output_name(self):
        rs = _make_runtime_state()
        result = rs.perform_config_action(
            "Ungroup Zones",
            zone="BT-W5 Akash",
        )
        self.assertIn("BT-W5 Akash", result)
        rs.roon_connection.ungroup_zones.assert_called_once()

    def test_ungroup_not_grouped_raises(self):
        rs = _make_runtime_state()
        with self.assertRaises(ValueError):
            rs.perform_config_action(
                "Ungroup Zones",
                zone="Chord Qutest",
            )

    def test_ungroup_unknown_raises(self):
        rs = _make_runtime_state()
        with self.assertRaises((ValueError, ZoneLookupError)):
            rs.perform_config_action(
                "Ungroup Zones",
                alias="Nonexistent",
                zone="Nonexistent",
            )

    def test_get_groups_shows_live_groups(self):
        rs = _make_runtime_state()
        result = rs.perform_config_action("Get Groups")
        self.assertIn("MDAC+ USB", result)
        self.assertIn("BT-W5 Akash", result)

    def test_get_groups_empty_when_none_grouped(self):
        rs = _make_runtime_state(zones=UNGROUPED_ZONES, outputs=UNGROUPED_OUTPUTS)
        result = rs.perform_config_action("Get Groups")
        self.assertIn("no zones", result.lower())


# ── Tests: Group-aware zone resolution ────────────────────────

class TestGroupAwareResolution(unittest.TestCase):
    """Test that resolve_zone_name checks group aliases after zone aliases."""

    def test_exact_zone_name_takes_priority(self):
        rs = _make_runtime_state()
        result = rs.resolve_zone_name("MDAC+ USB + 1")
        self.assertEqual(result, "MDAC+ USB + 1")

    def test_zone_alias_resolves_to_anchor_zone(self):
        rs = _make_runtime_state()
        rs.zone_aliases = {"Downstairs": "out_3"}
        result = rs.resolve_zone_name("Downstairs")
        self.assertEqual(result, "Chord Qutest")

    def test_member_name_resolves_to_containing_group(self):
        """Resolving a name that matches a member of a grouped zone
        returns the group's display_name — targeting a member addresses
        the group it's in."""
        rs = _make_runtime_state()
        result = rs.resolve_zone_name("BT-W5 Akash")
        self.assertEqual(result, "MDAC+ USB + 1")

    def test_ungrouped_output_resolves_normally(self):
        """An output that IS a zone (ungrouped) should resolve fine."""
        rs = _make_runtime_state()
        # "Chord Qutest" is both an output and a zone — should resolve
        result = rs.resolve_zone_name("Chord Qutest")
        self.assertEqual(result, "Chord Qutest")

class TestListZonesHandler(unittest.TestCase):
    """Test the list_zones roon-control-request handler."""

    def test_list_zones_includes_group_info(self):
        rs = _make_runtime_state()
        result = rs.execute_roon_control({"action": "list_zones"})
        grouped = next(z for z in result["zones"] if z["is_grouped"])
        self.assertEqual(grouped["group_members"], ["MDAC+ USB", "BT-W5 Akash"])

    def test_list_zones_includes_zone_alias(self):
        rs = _make_runtime_state()
        rs.zone_aliases = {"Study": "out_3"}
        result = rs.execute_roon_control({"action": "list_zones"})
        qutest = next(z for z in result["zones"] if z["display_name"] == "Chord Qutest")
        self.assertEqual(qutest["zone_alias"], "Study")

    def test_list_zones_default_first(self):
        rs = _make_runtime_state(target_zone="MDAC+ USB")
        result = rs.execute_roon_control({"action": "list_zones"})
        self.assertTrue(result["zones"][0]["is_default"])

    def test_set_default_zone_success(self):
        rs = _make_runtime_state()
        result = rs.execute_roon_control({"action": "set_default_zone", "zone": "Chord Qutest"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["zone"], "Chord Qutest")
        rs._broadcast_default_zone.assert_called_once()

    def test_set_default_zone_failure(self):
        rs = _make_runtime_state()
        result = rs.execute_roon_control({"action": "set_default_zone", "zone": "Nonexistent"})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


def tearDownModule():
    for td in _temp_dirs:
        td.cleanup()
    _temp_dirs.clear()


if __name__ == "__main__":
    unittest.main()
