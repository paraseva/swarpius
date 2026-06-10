"""Persistence tests for zone alias / group name / zone-group-id
JSON files — round-trips each so format and filtering rules stay
locked.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.runtime.state import RuntimeState


def _bare_runtime(tmp: Path) -> RuntimeState:
    """Shell RuntimeState with the three persistence paths redirected
    into a tempdir. Skips the real ``__init__``."""
    from tests._runtime_fixtures import wire_zone_domain
    rs = object.__new__(RuntimeState)
    rs.roon_connection = None
    rs._ws_send_callback = lambda _c, _p: None
    wire_zone_domain(rs, tmp_path=tmp)
    return rs


class TestZoneAliasesRoundTrip(unittest.TestCase):
    def test_save_then_load_preserves_entries(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases.update({"k": "Kitchen", "s": "Study"})
            rs._save_zone_aliases()

            rs2 = _bare_runtime(Path(td))
            rs2._load_zone_aliases()
            self.assertEqual(rs2.zone_aliases, {"k": "Kitchen", "s": "Study"})

    def test_missing_file_loads_empty(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs._load_zone_aliases()
            self.assertEqual(rs.zone_aliases, {})

    def test_malformed_file_loads_empty(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases_path.write_text("not-json{", encoding="utf-8")
            rs._load_zone_aliases()
            self.assertEqual(rs.zone_aliases, {})

    def test_blank_values_filtered_out(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases_path.write_text(
                json.dumps({"": "Kitchen", "valid": "", "good": "Study"}),
                encoding="utf-8",
            )
            rs._load_zone_aliases()
            self.assertEqual(rs.zone_aliases, {"good": "Study"})

    def test_non_dict_content_loads_empty(self):
        with TemporaryDirectory() as td:
            rs = _bare_runtime(Path(td))
            rs.zone_aliases_path.write_text("[1, 2, 3]", encoding="utf-8")
            rs._load_zone_aliases()
            self.assertEqual(rs.zone_aliases, {})


if __name__ == "__main__":
    unittest.main()
