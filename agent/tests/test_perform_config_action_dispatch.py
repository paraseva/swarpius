"""Gap-filler tests for ``RuntimeState.perform_config_action`` dispatch.

Enumerates every action branch the ``match`` statement handles, pinning
the call shape + return-string format. The Roon connection is mocked
at the API boundary (``make_mock_roon_connection`` — realistic
``api.zones`` and zone-lookup methods); ``ZoneDomain`` runs as
production code on top, so tests cover the real name-resolution /
alias / group-name logic that the dispatch leans on.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from app.exceptions import (
    RoonConnectionUnavailableError,
    UnsupportedActionError,
)
from app.runtime.state import RuntimeState

# Realistic zone data — covers every zone name used across the file.
# All 1-output (so by default ungrouped); tests that need a grouped
# zone replace the connection's outputs.
_DEFAULT_ZONES = {
    "z_lr": {
        "zone_id": "z_lr", "display_name": "Living Room",
        "outputs": [{"output_id": "o_lr", "display_name": "Living Room"}],
    },
    "z_k": {
        "zone_id": "z_k", "display_name": "Kitchen",
        "outputs": [{"output_id": "o_k", "display_name": "Kitchen"}],
    },
    "z_s": {
        "zone_id": "z_s", "display_name": "Study",
        "outputs": [{"output_id": "o_s", "display_name": "Study"}],
    },
}


def _make_group_zones_side_effect(conn):
    """Simulate the Roon Core's response to a group request: pop the
    given zones from ``api.zones`` and replace them with a single
    grouped zone whose outputs are the union of theirs (display_name
    inherited from the first zone, matching Roon's typical behaviour
    where the 'primary' zone keeps its name)."""
    def _group(zone_names):
        merged_outputs = []
        primary_name = zone_names[0] if zone_names else None
        for name in zone_names:
            for zid, z in list(conn.api.zones.items()):
                if z.get("display_name") == name:
                    merged_outputs.extend(z.get("outputs", []))
                    del conn.api.zones[zid]
                    break
        if primary_name and merged_outputs:
            new_zid = f"z_grp_{primary_name}"
            conn.api.zones[new_zid] = {
                "zone_id": new_zid,
                "display_name": primary_name,
                "outputs": merged_outputs,
            }
    return _group


def _make_ungroup_zones_side_effect(conn):
    """Simulate ungroup: split a grouped zone back into per-output zones."""
    def _ungroup(output_names):
        # Find the grouped zone whose output set matches output_names.
        wanted = {n.lower() for n in output_names}
        target_zid = None
        target_outputs = None
        for zid, z in list(conn.api.zones.items()):
            outs = z.get("outputs", [])
            if {o.get("display_name", "").lower() for o in outs} == wanted:
                target_zid = zid
                target_outputs = outs
                break
        if target_zid is None:
            return
        del conn.api.zones[target_zid]
        for out in target_outputs or []:
            name = out.get("display_name", "")
            new_zid = f"z_ungrp_{name}"
            conn.api.zones[new_zid] = {
                "zone_id": new_zid,
                "display_name": name,
                "outputs": [out],
            }
    return _ungroup


def _bare_runtime(tmp: Path, with_connection: bool = True) -> RuntimeState:
    """Shell RuntimeState wired for perform_config_action tests.
    Paths route to a tempdir so save_* calls are harmless; the Roon
    connection wraps ``make_mock_roon_connection`` (boundary-level
    fake) so production ``ZoneDomain`` logic runs."""
    from tests._runtime_fixtures import (
        make_mock_roon_connection,
        wire_config_action,
        wire_zone_domain,
    )
    rs = object.__new__(RuntimeState)

    if with_connection:
        # Deep-copy so per-test mutations don't leak into the module-level
        # _DEFAULT_ZONES dict (would break test isolation).
        conn = make_mock_roon_connection(zones=copy.deepcopy(_DEFAULT_ZONES))
        # Wrap ``set_default_zone`` so tests can assert on call args
        # while still letting the helper's real function update
        # ``conn.target_zone``.
        real_set_default_zone = conn.set_default_zone
        conn.set_default_zone = MagicMock(side_effect=real_set_default_zone)
        # Real Roon's ``group_zones`` / ``ungroup_zones`` update
        # ``api.zones`` synchronously (the python-roonapi library
        # mutates it before returning). Production
        # ``ConfigActionService`` relies on this — its post-dispatch
        # ``resolve_group_name`` call walks ``api.zones`` to confirm
        # the new grouping landed, and its "stale alias" cleanup path
        # deletes the entry we just saved if it can't find a matching
        # zone. Mirror that synchronous behaviour in the fake.
        conn.group_zones = MagicMock(
            side_effect=_make_group_zones_side_effect(conn),
        )
        conn.ungroup_zones = MagicMock(
            side_effect=_make_ungroup_zones_side_effect(conn),
        )
        rs.roon_connection = conn
    else:
        rs.roon_connection = None

    rs._ws_send_callback = lambda _c, _p: None
    wire_zone_domain(rs, tmp_path=tmp)
    rs._broadcast_zone_labels = lambda _z: None  # type: ignore[method-assign]
    wire_config_action(rs)
    return rs


class TestDispatchGuardrails(unittest.TestCase):
    def test_missing_connection_raises(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td), with_connection=False)
            with self.assertRaises(RoonConnectionUnavailableError):
                rs.perform_config_action("Set Default Zone", zone="Kitchen")

    def test_unknown_action_raises(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(UnsupportedActionError):
                rs.perform_config_action("Throw Toaster")


class TestSetDefaultZone(unittest.TestCase):
    def test_sets_and_returns_message(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            msg = rs.perform_config_action("Set Default Zone", zone="Kitchen")
            rs.roon_connection.set_default_zone.assert_called_once_with("Kitchen")
            self.assertIn("Kitchen", msg)

    def test_accepts_member_name_of_currently_grouped_zone(self):
        """Targeting a zone that's currently in a group resolves to the
        group's display_name — playing 'Kitchen' while Kitchen is
        grouped addresses the group it's in."""
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            del rs.roon_connection.api.zones["z_k"]
            rs.roon_connection.api.zones["z_grp"] = {
                "zone_id": "z_grp", "display_name": "Kitchen + 1",
                "outputs": [
                    {"output_id": "o_k", "display_name": "Kitchen"},
                    {"output_id": "o_other", "display_name": "Other"},
                ],
            }
            rs.perform_config_action("Set Default Zone", zone="Kitchen")
            rs.roon_connection.set_default_zone.assert_called_once_with("Kitchen + 1")


class TestSetZoneAlias(unittest.TestCase):
    def test_requires_zone_and_alias(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Set Zone Alias", zone="Kitchen")
            with self.assertRaises(ValueError):
                rs.perform_config_action("Set Zone Alias", alias="k")

    def test_rejects_alias_matching_existing_output_name_in_a_group(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.roon_connection.api.zones["z_grp"] = {
                "zone_id": "z_grp", "display_name": "Upstairs",
                "outputs": [
                    {"output_id": "o_a", "display_name": "Attic"},
                    {"output_id": "o_b", "display_name": "Loft"},
                ],
            }
            with self.assertRaises(ValueError) as ctx:
                rs.perform_config_action(
                    "Set Zone Alias", zone="Living Room", alias="Attic",
                )
            self.assertIn("already", str(ctx.exception).lower())

    def test_rejects_duplicate_alias_name(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.perform_config_action("Set Zone Alias", zone="Kitchen", alias="k")
            with self.assertRaises(ValueError) as ctx:
                rs.perform_config_action("Set Zone Alias", zone="Study", alias="k")
            self.assertIn("k", str(ctx.exception))

    def test_rejects_when_target_zone_is_grouped(self):
        """Structural check: zone has >1 outputs → refuse. The check looks at
        the zone's outputs list, not the display_name."""
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.roon_connection.api.zones["z_grp"] = {
                "zone_id": "z_grp", "display_name": "Upstairs",
                "outputs": [
                    {"output_id": "o_a", "display_name": "Attic"},
                    {"output_id": "o_b", "display_name": "Loft"},
                ],
            }
            with self.assertRaises(ValueError) as ctx:
                rs.perform_config_action(
                    "Set Zone Alias", zone="Upstairs", alias="upstairs",
                )
            self.assertIn("grouped", str(ctx.exception).lower())

    def test_allows_alias_on_output_inside_a_group(self):
        """Output names remain aliasable regardless of grouping state.
        The alias attaches to the output identity."""
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            # Replace standalone Kitchen with a group containing it.
            del rs.roon_connection.api.zones["z_k"]
            rs.roon_connection.api.zones["z_grp"] = {
                "zone_id": "z_grp", "display_name": "Upstairs",
                "outputs": [
                    {"output_id": "o_k", "display_name": "Kitchen"},
                    {"output_id": "o_other", "display_name": "Other"},
                ],
            }
            rs.perform_config_action("Set Zone Alias", zone="Kitchen", alias="k")
            # The alias resolves to the group's current display_name
            # because Kitchen is presently inside it.
            self.assertEqual(rs.zone_domain.resolve_alias("k"), "Upstairs")

    def test_rejects_unknown_target(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError) as ctx:
                rs.perform_config_action(
                    "Set Zone Alias", zone="DoesNotExist", alias="x",
                )
            self.assertIn("DoesNotExist", str(ctx.exception))

    def test_happy_path_resolves_via_alias(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            msg = rs.perform_config_action("Set Zone Alias", zone="Kitchen", alias="k")
            self.assertEqual(rs.zone_domain.resolve_alias("k"), "Kitchen")
            self.assertTrue(rs.zone_aliases_path.exists())
            self.assertIn("Kitchen", msg)


class TestRemoveZoneAlias(unittest.TestCase):
    def test_requires_alias(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Remove Zone Alias")

    def test_unknown_alias_raises(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Remove Zone Alias", alias="ghost")

    def test_happy_path_removes(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases = {"k": "Kitchen", "s": "Study"}
            msg = rs.perform_config_action("Remove Zone Alias", alias="K")  # case-insensitive
            self.assertEqual(rs.zone_aliases, {"s": "Study"})
            self.assertIn("k", msg)


class TestClearAllZoneAliases(unittest.TestCase):
    def test_clears(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases = {"k": "Kitchen"}
            msg = rs.perform_config_action("Clear All Zone Aliases")
            self.assertEqual(rs.zone_aliases, {})
            self.assertIn("cleared", msg)


class TestGetDefaultZone(unittest.TestCase):
    def test_none_set(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            msg = rs.perform_config_action("Get Default Zone")
            self.assertIn("No default zone", msg)

    def test_with_alias(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.roon_connection.target_zone = "Kitchen"
            rs.zone_aliases["k"] = "o_k"
            msg = rs.perform_config_action("Get Default Zone")
            self.assertIn("Kitchen", msg)
            self.assertIn("aliased as 'k'", msg)


class TestTransferZone(unittest.TestCase):
    def test_requires_target(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Transfer Zone", zone="Kitchen")

    def test_happy_path(self):
        """Transfer Zone moves playback but must NOT touch the default zone.
        See test_default_zone_broadcast.test_transfer_zone_does_not_change_default_or_broadcast
        for the broadcast contract.
        """
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.perform_config_action(
                "Transfer Zone", zone="Kitchen", zone_to_transfer_to="Study",
            )
            rs.roon_connection.transfer_zone.assert_called_once_with("Kitchen", "Study")
            rs.roon_connection.set_default_zone.assert_not_called()

    def test_falls_back_to_default_zone_as_source(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.roon_connection.target_zone = "Kitchen"
            rs.perform_config_action(
                "Transfer Zone", zone_to_transfer_to="Study",
            )
            rs.roon_connection.transfer_zone.assert_called_once_with("Kitchen", "Study")


class TestGetZoneAliases(unittest.TestCase):
    def test_none_set(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            msg = rs.perform_config_action("Get Zone Aliases")
            self.assertIn("No zone aliases", msg)

    def test_lists_all(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases = {"k": "o_k", "s": "o_s"}
            msg = rs.perform_config_action("Get Zone Aliases")
            self.assertIn("k: Kitchen", msg)
            self.assertIn("s: Study", msg)


class TestRenameZoneAlias(unittest.TestCase):
    def test_requires_both_names(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Rename Zone Alias", alias="k")

    def test_unknown_alias_raises(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Rename Zone Alias", alias="ghost", new_name="x")

    def test_happy_path(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases = {"k": "o_k"}
            rs.perform_config_action("Rename Zone Alias", alias="k", new_name="kit")
            self.assertEqual(rs.zone_aliases, {"kit": "o_k"})


class TestGroupZones(unittest.TestCase):
    def test_requires_at_least_two(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Group Zones", group_zones=["A"])

    def test_groups_outputs(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            msg = rs.perform_config_action(
                "Group Zones", group_zones=["Kitchen", "Study"],
            )
            rs.roon_connection.group_zones.assert_called_once_with(["Kitchen", "Study"])
            self.assertIn("Kitchen", msg)
            self.assertIn("Study", msg)


class TestUngroupZones(unittest.TestCase):
    def test_requires_target(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            with self.assertRaises(ValueError):
                rs.perform_config_action("Ungroup Zones")

    def test_errors_when_not_grouped(self):
        """Ungroup against an ungrouped zone (single output) should raise."""
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            # Default _DEFAULT_ZONES: Kitchen has a single output — not grouped.
            with self.assertRaises(ValueError):
                rs.perform_config_action("Ungroup Zones", alias="Kitchen")

    def test_ungroups_and_returns_message(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            # Add a real grouped "Upstairs" zone with two outputs so
            # production resolve_zone_for_ungroup + get_zone_snapshot
            # find it naturally.
            rs.roon_connection.api.zones["z_up"] = {
                "zone_id": "z_up", "display_name": "Upstairs",
                "outputs": [
                    {"output_id": "o_k2", "display_name": "Kitchen"},
                    {"output_id": "o_s2", "display_name": "Study"},
                ],
            }
            msg = rs.perform_config_action("Ungroup Zones", alias="Upstairs")
            rs.roon_connection.ungroup_zones.assert_called_once_with(
                ["Kitchen", "Study"],
            )
            self.assertIn("Upstairs", msg)

class TestGetGroups(unittest.TestCase):
    def test_none_grouped(self):
        """All zones in _DEFAULT_ZONES have a single output — none grouped."""
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            msg = rs.perform_config_action("Get Groups")
            self.assertIn("No zones", msg)

    def test_lists_grouped(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.roon_connection.api.zones["z_up"] = {
                "zone_id": "z_up", "display_name": "Upstairs",
                "outputs": [
                    {"output_id": "o_k2", "display_name": "Kitchen"},
                    {"output_id": "o_s2", "display_name": "Study"},
                ],
            }
            msg = rs.perform_config_action("Get Groups")
            self.assertIn("Upstairs", msg)
            self.assertIn("Kitchen", msg)
            self.assertIn("Study", msg)


if __name__ == "__main__":
    unittest.main()
