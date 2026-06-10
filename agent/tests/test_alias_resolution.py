"""Behavioural tests for the output-anchored zone alias model.

An alias maps a friendly name to a single output_id. Resolution to a
display_name is dynamic: at any moment, the alias resolves to the
display_name of whichever zone currently contains the anchor output.
Group / ungroup / online-offline transitions of that zone are
followed automatically.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.roon.zone_domain import ZoneDomain  # noqa: E402


def _zone(zone_id, display_name, outputs):
    return {
        "zone_id": zone_id,
        "display_name": display_name,
        "outputs": [{"output_id": oid, "display_name": dn} for oid, dn in outputs],
    }


def _conn(zones):
    conn = MagicMock()
    conn.api.zones = zones
    return conn


def _make_domain(zones, tmp: Path) -> ZoneDomain:
    conn = _conn(zones)
    return ZoneDomain(
        zone_aliases_path=tmp / "zone_aliases.json",
        get_roon_connection=lambda: conn,
        ws_send=lambda _c, _p: None,
        get_last_played_dict=None,
    )


class TestAliasResolution(unittest.TestCase):
    def test_alias_resolves_to_standalone_zone_display_name(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            self.assertEqual(d.resolve_alias("lounge"), "Speakers")

    def test_alias_follows_anchor_into_group(self):
        zones = {
            "z_grp": _zone("z_grp", "Speakers + 1",
                           [("o_a", "Speakers"), ("o_b", "Kitchen")]),
        }
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            self.assertEqual(d.resolve_alias("lounge"), "Speakers + 1")

    def test_alias_returns_none_when_anchor_offline(self):
        zones = {"z_other": _zone("z_other", "Kitchen", [("o_k", "Kitchen")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_offline"
            self.assertIsNone(d.resolve_alias("lounge"))

    def test_alias_recovers_when_anchor_returns(self):
        with TemporaryDirectory() as td:
            d = _make_domain({}, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            self.assertIsNone(d.resolve_alias("lounge"))

            d._get_connection().api.zones = {
                "z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")]),
            }
            self.assertEqual(d.resolve_alias("lounge"), "Speakers")

    def test_alias_loaded_from_disk_when_anchor_offline_is_kept(self):
        """When ``load_zone_aliases`` runs and the anchor output isn't
        in the current zone list, the alias must be kept with the saved
        display_name as a placeholder so it survives temporary
        disconnections."""
        zones = {"z_other": _zone("z_other", "Kitchen", [("o_k", "Kitchen")])}
        with TemporaryDirectory() as td:
            path = Path(td) / "zone_aliases.json"
            path.write_text(json.dumps({"fold": "Z Fold 5"}))
            conn = _conn(zones)
            d = ZoneDomain(
                zone_aliases_path=path,
                get_roon_connection=lambda: conn,
                ws_send=lambda _c, _p: None,
            )
            d.load_zone_aliases()
            self.assertIn("fold", d.zone_aliases)
            self.assertEqual(d.zone_aliases["fold"], "Z Fold 5")
            self.assertIsNone(d.resolve_alias("fold"))

    def test_offline_loaded_alias_promotes_to_output_id_when_zone_returns(self):
        """An alias loaded with a display_name placeholder gets promoted
        to its proper output_id anchor the first time the zone is
        visible during resolution. Re-saves the file so the on-disk
        anchor stays valid."""
        with TemporaryDirectory() as td:
            path = Path(td) / "zone_aliases.json"
            path.write_text(json.dumps({"fold": "Z Fold 5"}))
            conn = _conn({})
            d = ZoneDomain(
                zone_aliases_path=path,
                get_roon_connection=lambda: conn,
                ws_send=lambda _c, _p: None,
            )
            d.load_zone_aliases()
            self.assertEqual(d.zone_aliases["fold"], "Z Fold 5")

            d._get_connection().api.zones = {
                "z_fold": _zone(
                    "z_fold", "Z Fold 5", [("o_fold", "Z Fold 5")],
                ),
            }
            self.assertEqual(d.resolve_alias("fold"), "Z Fold 5")
            self.assertEqual(d.zone_aliases["fold"], "o_fold")

    def test_alias_lookup_case_insensitive(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["Lounge"] = "o_a"
            self.assertEqual(d.resolve_alias("LOUNGE"), "Speakers")
            self.assertEqual(d.resolve_alias("lounge"), "Speakers")

    def test_resolve_returns_none_for_unknown_alias(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            self.assertIsNone(d.resolve_alias("nope"))


class TestAliasDisplayCache(unittest.TestCase):
    """The alias display cache is the LLM-facing 'last-known name' so
    aliases keep displaying a meaningful zone name even when their
    underlying zone is currently offline."""

    def test_cache_populated_on_load_from_disk(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            path = Path(td) / "zone_aliases.json"
            path.write_text(json.dumps({"lounge": "Speakers"}))
            conn = _conn(zones)
            d = ZoneDomain(
                zone_aliases_path=path,
                get_roon_connection=lambda: conn,
                ws_send=lambda _c, _p: None,
            )
            d.load_zone_aliases()
            self.assertEqual(d._alias_display_cache["lounge"], "Speakers")

    def test_cache_refreshes_when_zone_display_name_changes(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            d._alias_display_cache["lounge"] = "Speakers"
            zones["z_a"]["display_name"] = "Lounge"
            d.resolve_alias("lounge")
            self.assertEqual(d._alias_display_cache["lounge"], "Lounge")

    def test_cache_refreshes_when_anchor_output_is_grouped(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            d._alias_display_cache["lounge"] = "Speakers"
            d._get_connection().api.zones = {
                "z_grp": _zone(
                    "z_grp", "Speakers + 1",
                    [("o_a", "Speakers"), ("o_b", "Other")],
                ),
            }
            d.resolve_alias("lounge")
            self.assertEqual(d._alias_display_cache["lounge"], "Speakers + 1")

    def test_get_alias_display_name_live_when_online(self):
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            self.assertEqual(d.get_alias_display_name("lounge"), "Speakers")

    def test_get_alias_display_name_cached_when_offline(self):
        with TemporaryDirectory() as td:
            d = _make_domain({}, Path(td))
            d.zone_aliases["lounge"] = "o_offline"
            d._alias_display_cache["lounge"] = "Speakers"
            self.assertEqual(d.get_alias_display_name("lounge"), "Speakers")

    def test_get_alias_display_name_prefers_live_over_stale_cache(self):
        zones = {"z_a": _zone("z_a", "Lounge", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            d._alias_display_cache["lounge"] = "Speakers"
            self.assertEqual(d.get_alias_display_name("lounge"), "Lounge")

    def test_get_alias_display_name_returns_none_for_unknown(self):
        with TemporaryDirectory() as td:
            d = _make_domain({}, Path(td))
            self.assertIsNone(d.get_alias_display_name("nope"))

    def test_get_alias_for_zone_survives_offline_via_cache(self):
        """Default-zone broadcasts run get_alias_for_zone(zone_name)
        to label the badge. When the zone goes offline, the live lookup
        in api.zones fails; the alias must still resolve from the
        last-known cache so the badge keeps its alias label."""
        zones = {"z_a": _zone("z_a", "Speakers", [("o_a", "Speakers")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_a"
            # Online — resolves and warms the cache.
            self.assertEqual(d.get_alias_for_zone("Speakers"), "lounge")
            # Zone vanishes from Roon's zone list (offline).
            d._get_connection().api.zones = {}
            self.assertEqual(d.get_alias_for_zone("Speakers"), "lounge")


class TestAliasPersistencePolicy(unittest.TestCase):
    """On-disk format maps alias → individual output's display_name
    (never a grouped zone's display_name). Auto-refreshes when the
    output's name changes in Roon so a later restart still recovers
    the alias."""

    def test_disk_uses_output_display_name_when_zone_is_grouped(self):
        zones = {
            "z_grp": _zone(
                "z_grp", "Living Room + 1",
                [("o_lr", "Living Room"), ("o_o", "Other")],
            ),
        }
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_lr"
            d._alias_output_name_cache["lounge"] = "Living Room"
            d.save_zone_aliases()
            saved = json.loads(d.zone_aliases_path.read_text())
            self.assertEqual(saved, {"lounge": "Living Room"})

    def test_disk_uses_cached_output_name_when_output_offline(self):
        with TemporaryDirectory() as td:
            d = _make_domain({}, Path(td))
            d.zone_aliases["lounge"] = "o_offline"
            d._alias_output_name_cache["lounge"] = "Living Room"
            d.save_zone_aliases()
            saved = json.loads(d.zone_aliases_path.read_text())
            self.assertEqual(saved, {"lounge": "Living Room"})

    def test_disk_updates_when_output_renamed_in_roon(self):
        zones = {"z_a": _zone("z_a", "Living Room", [("o_lr", "Living Room")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_lr"
            d._alias_output_name_cache["lounge"] = "Living Room"
            d.save_zone_aliases()
            zones["z_a"]["display_name"] = "Lounge"
            zones["z_a"]["outputs"][0]["display_name"] = "Lounge"
            d.resolve_alias("lounge")
            saved = json.loads(d.zone_aliases_path.read_text())
            self.assertEqual(saved, {"lounge": "Lounge"})

    def test_resolve_doesnt_rewrite_disk_when_output_name_unchanged(self):
        zones = {"z_a": _zone("z_a", "Living Room", [("o_lr", "Living Room")])}
        with TemporaryDirectory() as td:
            d = _make_domain(zones, Path(td))
            d.zone_aliases["lounge"] = "o_lr"
            d._alias_output_name_cache["lounge"] = "Living Room"
            d.save_zone_aliases()
            disk_before = d.zone_aliases_path.read_text()
            d.resolve_alias("lounge")
            self.assertEqual(d.zone_aliases_path.read_text(), disk_before)

    def test_load_populates_output_name_cache(self):
        zones = {"z_a": _zone("z_a", "Living Room", [("o_lr", "Living Room")])}
        with TemporaryDirectory() as td:
            path = Path(td) / "zone_aliases.json"
            path.write_text(json.dumps({"lounge": "Living Room"}))
            conn = _conn(zones)
            d = ZoneDomain(
                zone_aliases_path=path,
                get_roon_connection=lambda: conn,
                ws_send=lambda _c, _p: None,
            )
            d.load_zone_aliases()
            self.assertEqual(d._alias_output_name_cache["lounge"], "Living Room")

if __name__ == "__main__":
    unittest.main()
