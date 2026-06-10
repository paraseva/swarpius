"""The app version is sourced from a single file (agent/VERSION).

``data_paths.app_version()`` reads it (bundled at the agent root), and
the Roon authorisation dialog shows it via ``connection.APP_INFO``.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app import data_paths  # noqa: E402


class TestAppVersion(unittest.TestCase):
    def test_reads_version_file_from_agent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "VERSION").write_text("9.8.7\n", encoding="utf-8")
            with patch.object(data_paths, "AGENT_ROOT", Path(tmp)):
                self.assertEqual(data_paths.app_version(), "9.8.7")

    def test_falls_back_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(data_paths, "AGENT_ROOT", Path(tmp)):
                self.assertEqual(data_paths.app_version(), "0.0.0")

    def test_matches_the_shipped_version_file(self):
        shipped = (data_paths.AGENT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(data_paths.app_version(), shipped)

    def test_connection_reports_the_same_version(self):
        """The Roon authorisation dialog must show the SSoT version."""
        from roon_core.connection import APP_INFO
        self.assertEqual(APP_INFO["display_version"], data_paths.app_version())


if __name__ == "__main__":
    unittest.main()
