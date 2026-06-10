"""Behavioural tests for the output-anchored default zone model.

``target_zone`` resolves dynamically: it always reflects the
display_name of whichever zone currently contains the preferred output.
Grouping / ungrouping / online / offline of the underlying zone is
followed automatically, with no reconcile step needed.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.exceptions import ZoneLookupError  # noqa: E402
from roon_core.zones import RoonZoneMixin  # noqa: E402


def _zones_standalone(*names: str) -> dict:
    return {
        f"z_{n}": {
            "zone_id": f"z_{n}",
            "display_name": n,
            "outputs": [{"output_id": f"o_{n}", "display_name": n}],
        }
        for n in names
    }


def _zones_grouped(group_display_name: str, *member_names: str) -> dict:
    primary = member_names[0]
    return {
        f"z_grp_{primary}": {
            "zone_id": f"z_grp_{primary}",
            "display_name": group_display_name,
            "outputs": [
                {"output_id": f"o_{n}", "display_name": n}
                for n in member_names
            ],
        },
    }


class _Conn(RoonZoneMixin):
    def __init__(self, zones: dict, default_zone_name: str | None = None) -> None:
        self.api = SimpleNamespace(zones=zones)
        self._default_zone_name = default_zone_name
        self._preferred_output_id: str | None = None


class TestStartupResolution(unittest.TestCase):
    def test_resolves_name_matching_standalone_zone(self):
        conn = _Conn(
            zones=_zones_standalone("Speakers", "Kitchen"),
            default_zone_name="Speakers",
        )
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers")

    def test_resolves_name_matching_grouped_zone_display_name(self):
        conn = _Conn(
            zones=_zones_grouped("Speakers + 1", "Speakers", "Kitchen"),
            default_zone_name="Speakers + 1",
        )
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers + 1")

    def test_resolves_name_matching_output_inside_group(self):
        conn = _Conn(
            zones=_zones_grouped("Speakers + 1", "Speakers", "Kitchen"),
            default_zone_name="Kitchen",
        )
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers + 1")

    def test_unknown_name_falls_back_to_first_reported_output(self):
        conn = _Conn(
            zones=_zones_standalone("Speakers", "Kitchen"),
            default_zone_name="Nonexistent",
        )
        conn._resolve_default_zone()
        self.assertIn(conn.target_zone, {"Speakers", "Kitchen"})
        self.assertIsNotNone(conn.target_zone)

    def test_unset_default_falls_back_to_first_reported_output(self):
        conn = _Conn(zones=_zones_standalone("Speakers"), default_zone_name=None)
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers")

    def test_no_zones_reported_leaves_default_unresolved(self):
        conn = _Conn(zones={}, default_zone_name="Speakers")
        conn._resolve_default_zone()
        self.assertIsNone(conn.target_zone)


class TestDynamicResolution(unittest.TestCase):
    def test_standalone_default_follows_when_subsumed_into_group(self):
        conn = _Conn(
            zones=_zones_standalone("Speakers", "Kitchen"),
            default_zone_name="Speakers",
        )
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers")

        conn.api.zones = _zones_grouped("Speakers + 1", "Speakers", "Kitchen")
        self.assertEqual(conn.target_zone, "Speakers + 1")

    def test_group_default_follows_when_fully_dismantled(self):
        conn = _Conn(
            zones=_zones_grouped("Speakers + 1", "Speakers", "Kitchen"),
            default_zone_name="Speakers + 1",
        )
        conn._resolve_default_zone()

        conn.api.zones = _zones_standalone("Speakers", "Kitchen")
        self.assertEqual(conn.target_zone, "Speakers")

    def test_group_default_follows_partial_ungroup(self):
        conn = _Conn(
            zones=_zones_grouped("Speakers + 2", "Speakers", "Kitchen", "Study"),
            default_zone_name="Speakers + 2",
        )
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers + 2")

        conn.api.zones = {
            **_zones_grouped("Speakers + 1", "Speakers", "Kitchen"),
            **_zones_standalone("Study"),
        }
        self.assertEqual(conn.target_zone, "Speakers + 1")

    def test_default_lands_on_base_when_base_removed_from_group(self):
        conn = _Conn(
            zones=_zones_grouped("Speakers + 2", "Speakers", "Kitchen", "Study"),
            default_zone_name="Speakers + 2",
        )
        conn._resolve_default_zone()

        conn.api.zones = {
            **_zones_grouped("Kitchen + 1", "Kitchen", "Study"),
            **_zones_standalone("Speakers"),
        }
        self.assertEqual(conn.target_zone, "Speakers")

    def test_target_zone_none_when_preferred_output_offline(self):
        conn = _Conn(
            zones=_zones_standalone("Speakers", "Kitchen"),
            default_zone_name="Speakers",
        )
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers")

        conn.api.zones = _zones_standalone("Kitchen")
        self.assertIsNone(conn.target_zone)

    def test_target_zone_recovers_when_preferred_output_returns(self):
        conn = _Conn(zones={}, default_zone_name="Speakers")
        conn._resolve_default_zone()
        self.assertIsNone(conn.target_zone)

        conn.api.zones = _zones_standalone("Speakers")
        conn._resolve_default_zone()
        self.assertEqual(conn.target_zone, "Speakers")


class TestSetDefaultZone(unittest.TestCase):
    def test_set_by_standalone_zone_name(self):
        conn = _Conn(zones=_zones_standalone("Speakers", "Kitchen"))
        conn.set_default_zone("Kitchen")
        self.assertEqual(conn.target_zone, "Kitchen")

    def test_set_by_group_display_name_anchors_on_base(self):
        conn = _Conn(zones=_zones_grouped("Speakers + 1", "Speakers", "Kitchen"))
        conn.set_default_zone("Speakers + 1")
        self.assertEqual(conn.target_zone, "Speakers + 1")
        conn.api.zones = _zones_standalone("Speakers", "Kitchen")
        self.assertEqual(conn.target_zone, "Speakers")

    def test_set_by_output_name_inside_group_anchors_on_that_output(self):
        conn = _Conn(zones=_zones_grouped("Speakers + 1", "Speakers", "Kitchen"))
        conn.set_default_zone("Kitchen")
        self.assertEqual(conn.target_zone, "Speakers + 1")
        conn.api.zones = _zones_standalone("Speakers", "Kitchen")
        self.assertEqual(conn.target_zone, "Kitchen")

    def test_set_to_unknown_name_raises_and_keeps_previous(self):
        conn = _Conn(zones=_zones_standalone("Speakers", "Kitchen"))
        conn.set_default_zone("Speakers")
        with self.assertRaises(ZoneLookupError):
            conn.set_default_zone("Nonexistent")
        self.assertEqual(conn.target_zone, "Speakers")


if __name__ == "__main__":
    unittest.main()
