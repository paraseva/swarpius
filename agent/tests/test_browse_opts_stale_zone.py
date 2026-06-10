"""Stale-default fallback in ``_build_browse_opts``.

Inherits both ``RoonBrowseMixin`` and ``RoonZoneMixin`` so production
``_lookup_output_id`` runs on the call path against a realistic
``api.zones`` dict.
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
from roon_core.browse import RoonBrowseMixin  # noqa: E402
from roon_core.zones import RoonZoneMixin  # noqa: E402


class _Conn(RoonBrowseMixin, RoonZoneMixin):
    def __init__(self, zones: dict, preferred_output_id: str | None) -> None:
        self.api = SimpleNamespace(zones=zones)
        self._preferred_output_id = preferred_output_id


_ZONES = {
    "z_lr": {
        "zone_id": "z_lr",
        "display_name": "Living Room",
        "outputs": [{"output_id": "o_lr", "display_name": "Living Room"}],
    },
    "z_k": {
        "zone_id": "z_k",
        "display_name": "Kitchen",
        "outputs": [{"output_id": "o_k", "display_name": "Kitchen"}],
    },
}


class TestBuildBrowseOptsStaleDefault(unittest.TestCase):
    def test_valid_default_resolves_normally(self):
        conn = _Conn(_ZONES, preferred_output_id="o_k")
        opts = conn._build_browse_opts(zone=None, session_key=None)
        self.assertEqual(opts["zone_or_output_id"], "z_k")
        self.assertEqual(opts["hierarchy"], "search")

    def test_offline_default_falls_back_to_any_output(self):
        conn = _Conn(_ZONES, preferred_output_id="o_phantom")
        opts = conn._build_browse_opts(zone=None, session_key=None)
        self.assertIn(opts["zone_or_output_id"], {"o_lr", "o_k"})

    def test_explicit_unknown_zone_still_raises(self):
        conn = _Conn(_ZONES, preferred_output_id="o_k")
        with self.assertRaises(ZoneLookupError):
            conn._build_browse_opts(zone="No Such Zone", session_key=None)

    def test_session_key_threaded_through(self):
        conn = _Conn(_ZONES, preferred_output_id="o_k")
        opts = conn._build_browse_opts(zone=None, session_key="sess-123")
        self.assertEqual(opts["multi_session_key"], "sess-123")

    def test_no_zones_at_all_raises(self):
        conn = _Conn({}, preferred_output_id="o_phantom")
        with self.assertRaises(ZoneLookupError):
            conn._build_browse_opts(zone=None, session_key=None)


if __name__ == "__main__":
    unittest.main()
