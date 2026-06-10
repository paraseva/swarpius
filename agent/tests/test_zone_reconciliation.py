"""Tests for zone state reconciliation.

_reconcile_zone_state() detects renames, composition changes, and removed
zones by diffing a snapshot of api.zones against a cached previous snapshot,
then updates target_zone, zone_aliases, and group_names accordingly.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# ── Helpers ───────────────────────────────────────────────────

_temp_dirs: list[tempfile.TemporaryDirectory] = []


def _make_runtime_state(zones, target_zone=None, zone_aliases=None, zone_cache=None):
    from app.runtime.state import RuntimeState
    from tests._runtime_fixtures import (
        make_mock_roon_connection,
        wire_config_action,
        wire_zone_domain,
    )

    rs = object.__new__(RuntimeState)
    rs.roon_connection = make_mock_roon_connection(zones, target_zone=target_zone)
    rs._ws_send_callback = MagicMock()

    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    wire_zone_domain(rs, tmp_path=Path(td.name))

    if zone_aliases:
        rs.zone_aliases.update(zone_aliases)
    if zone_cache:
        rs._zone_cache.update(zone_cache)

    rs._broadcast_default_zone = MagicMock()
    rs._broadcast_zone_labels = MagicMock()
    wire_config_action(rs)
    return rs


# ── Zone data builders ────────────────────────────────────────

def _zone(zone_id, display_name, outputs, state="stopped"):
    return {
        "zone_id": zone_id,
        "display_name": display_name,
        "state": state,
        "outputs": [
            {"output_id": oid, "display_name": oname, "zone_id": zone_id}
            for oid, oname in outputs
        ],
    }


# ── Tests: Zone renames ──────────────────────────────────────

class TestZoneRename(unittest.TestCase):

    def test_alias_follows_zone_rename_via_dynamic_resolution(self):
        old_cache = {"z1": {"display_name": "Old Name", "outputs": {"o1": "Old Name"}}}
        new_zones = {
            "z1": _zone("z1", "New Name", [("o1", "New Name")]),
        }
        rs = _make_runtime_state(
            zones=new_zones,
            zone_aliases={"My Alias": "o1"},
            zone_cache=old_cache,
        )
        rs._reconcile_zone_state()
        self.assertEqual(rs.zone_aliases["My Alias"], "o1")
        self.assertEqual(rs.zone_domain.resolve_alias("My Alias"), "New Name")

    def test_zone_rename_no_false_positive(self):
        """No changes when zone names haven't changed."""
        zones = {"z1": _zone("z1", "Same", [("o1", "Same")])}
        cache = {"z1": {"display_name": "Same", "outputs": {"o1": "Same"}}}
        rs = _make_runtime_state(zones=zones, target_zone="Same", zone_cache=cache)
        rs._reconcile_zone_state()
        rs._broadcast_default_zone.assert_not_called()


# ── Tests: Default-zone offline broadcasts ───────────────────

class TestDefaultZoneOnlineBroadcast(unittest.TestCase):
    """Reconciliation must broadcast default-zone-update when the
    chosen default zone's online state flips, even though target_zone
    itself didn't change. This is what lets the dropdown render the
    badge red when the zone goes offline and back to normal when it
    returns.
    """

    @staticmethod
    def _default_zone_broadcasts(rs) -> list:
        from app.constants import CHANNEL_DEFAULT_ZONE_UPDATE
        return [
            call for call in rs._ws_send_callback.call_args_list
            if call.args[0] == CHANNEL_DEFAULT_ZONE_UPDATE
        ]

    def test_broadcasts_when_default_zone_goes_offline(self):
        # Initial cache: default zone present.
        old_cache = {
            "z_a": {"display_name": "A", "outputs": {"o1": "A"}},
            "z_b": {"display_name": "B", "outputs": {"o2": "B"}},
        }
        # New snapshot: zone "A" disappears (e.g. BT headphones standby).
        new_zones = {
            "z_b": _zone("z_b", "B", [("o2", "B")]),
        }
        rs = _make_runtime_state(
            zones=new_zones,
            target_zone="A",
            zone_cache=old_cache,
        )
        # Prime prior state to (name, online=True) so the transition is
        # observable. Without priming, the first reconcile is treated
        # as initial observation and stays silent.
        rs.zone_domain._last_default_zone_state = ("A", True)

        rs._reconcile_zone_state()

        broadcasts = self._default_zone_broadcasts(rs)
        self.assertEqual(len(broadcasts), 1)
        payload = broadcasts[0].args[1]
        self.assertEqual(payload["zone_name"], "A")
        self.assertFalse(payload["is_online"])

    def test_broadcasts_when_default_zone_comes_back_online(self):
        # Initial: zone "A" missing.
        old_cache = {
            "z_b": {"display_name": "B", "outputs": {"o2": "B"}},
        }
        # New snapshot: zone "A" returns.
        new_zones = {
            "z_a": _zone("z_a", "A", [("o1", "A")]),
            "z_b": _zone("z_b", "B", [("o2", "B")]),
        }
        rs = _make_runtime_state(
            zones=new_zones,
            target_zone="A",
            zone_cache=old_cache,
        )
        rs.zone_domain._last_default_zone_state = ("A", False)

        rs._reconcile_zone_state()

        broadcasts = self._default_zone_broadcasts(rs)
        self.assertEqual(len(broadcasts), 1)
        self.assertTrue(broadcasts[0].args[1]["is_online"])

    def test_no_broadcast_when_default_zone_stays_offline(self):
        old_cache = {
            "z_b": {"display_name": "B", "outputs": {"o2": "B"}},
        }
        new_zones = {
            "z_b": _zone("z_b", "B", [("o2", "B")]),
        }
        rs = _make_runtime_state(
            zones=new_zones,
            target_zone="A",  # offline before, offline now
            zone_cache=old_cache,
        )
        rs.zone_domain._last_default_zone_state = ("A", False)

        rs._reconcile_zone_state()

        self.assertEqual(self._default_zone_broadcasts(rs), [])

    def test_no_broadcast_on_first_observation(self):
        """The first reconcile after startup shouldn't fire a redundant
        broadcast — the initial WS connect snapshot has already covered it.
        """
        zones = {"z_a": _zone("z_a", "A", [("o1", "A")])}
        rs = _make_runtime_state(
            zones=zones,
            target_zone="A",
            zone_cache={"z_a": {"display_name": "A", "outputs": {"o1": "A"}}},
        )

        rs._reconcile_zone_state()

        self.assertEqual(self._default_zone_broadcasts(rs), [])

    def test_broadcasts_when_default_zone_display_name_changes(self):
        """If the default output is grouped (or otherwise lands in a
        differently-named zone) the resolved display_name changes
        while online state stays True. The UI dropdown follows only
        if reconcile broadcasts on this transition."""
        old_cache = {
            "z_a": {"display_name": "A", "outputs": {"o1": "A"}},
            "z_b": {"display_name": "B", "outputs": {"o2": "B"}},
        }
        new_zones = {
            "z_grp": _zone("z_grp", "A + 1", [("o1", "A"), ("o2", "B")]),
        }
        rs = _make_runtime_state(
            zones=new_zones,
            target_zone="A + 1",
            zone_cache=old_cache,
        )
        rs.zone_domain._last_default_zone_state = ("A", True)

        rs._reconcile_zone_state()

        broadcasts = self._default_zone_broadcasts(rs)
        self.assertEqual(len(broadcasts), 1)
        payload = broadcasts[0].args[1]
        self.assertEqual(payload["zone_name"], "A + 1")
        self.assertTrue(payload["is_online"])


# ── Tests: Cache initialization ──────────────────────────────

class TestCacheInit(unittest.TestCase):

    def test_build_zone_cache(self):
        zones = {
            "z1": _zone("z1", "A + 1", [("o1", "A"), ("o2", "B")]),
            "z2": _zone("z2", "C", [("o3", "C")]),
        }
        rs = _make_runtime_state(zones=zones)
        cache = rs._build_zone_cache()
        self.assertEqual(cache["z1"]["display_name"], "A + 1")
        self.assertEqual(cache["z1"]["outputs"], {"o1": "A", "o2": "B"})
        self.assertEqual(cache["z2"]["outputs"], {"o3": "C"})


def tearDownModule():
    for td in _temp_dirs:
        td.cleanup()
    _temp_dirs.clear()
