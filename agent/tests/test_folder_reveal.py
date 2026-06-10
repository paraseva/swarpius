"""Tests for ``app.io.folder_reveal`` — revealing the stop-marker folder
in the OS file manager.

Bundle-only: the desktop app and the browser share a machine, so the
agent can open the folder for the user. Refused in source / Docker /
headless modes, where shelling out to a file manager is either pointless
or a remote-trigger hazard."""

import os
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
from app.io import folder_reveal  # noqa: E402


class TestOpenStopMarkerFolder(unittest.TestCase):

    def test_refused_when_not_running_from_bundle(self):
        calls = []
        with patch.object(data_paths, "_running_from_bundle", return_value=False), \
             patch.object(data_paths, "_running_in_docker", return_value=False):
            result = folder_reveal.open_stop_marker_folder(
                platform="linux", opener=lambda p, pl: calls.append((p, pl)),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [])

    def test_refused_in_docker_even_when_frozen(self):
        calls = []
        with patch.object(data_paths, "_running_from_bundle", return_value=True), \
             patch.object(data_paths, "_running_in_docker", return_value=True):
            result = folder_reveal.open_stop_marker_folder(
                platform="linux", opener=lambda p, pl: calls.append((p, pl)),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [])

    def test_opens_stop_marker_dir_on_bundle(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(data_paths, "_running_from_bundle", return_value=True), \
                 patch.object(data_paths, "_running_in_docker", return_value=False), \
                 patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                result = folder_reveal.open_stop_marker_folder(
                    platform="linux", opener=lambda p, pl: calls.append((p, pl)),
                )
            self.assertTrue(result["ok"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], Path(tmp) / "Stop Simulation")
            # The folder is created so the file manager has something to open.
            self.assertTrue((Path(tmp) / "Stop Simulation").is_dir())

    def test_reports_error_when_opener_raises(self):
        def boom(_path, _platform):
            raise OSError("no display")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(data_paths, "_running_from_bundle", return_value=True), \
                 patch.object(data_paths, "_running_in_docker", return_value=False), \
                 patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                result = folder_reveal.open_stop_marker_folder(
                    platform="linux", opener=boom,
                )
        self.assertFalse(result["ok"])

    def test_default_opener_dispatches_per_platform(self):
        target = Path("/some/folder")
        with patch("app.io.folder_reveal.subprocess.run") as run:
            folder_reveal._default_opener(target, "darwin")
            run.assert_called_once_with(["open", str(target)], check=False)
        with patch("app.io.folder_reveal.subprocess.run") as run:
            folder_reveal._default_opener(target, "linux")
            run.assert_called_once_with(["xdg-open", str(target)], check=False)


if __name__ == "__main__":
    unittest.main()
