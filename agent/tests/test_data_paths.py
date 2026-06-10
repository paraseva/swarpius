"""Tests for the data_paths module — centralised data directory resolution."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.data_paths import (
    AGENT_ROOT,
    RESTART_RESPAWN_ENV,
    analysis_dir,
    config_dir,
    conversation_logs_dir,
    data_dir,
    ensure_dirs,
    messages_db_path,
    server_logs_dir,
    should_auto_open_browser,
)


class TestDataDir(unittest.TestCase):
    """data_dir() resolves from SWARPIUS_DATA_DIR or defaults to <agent_root>/data."""

    @patch.dict(os.environ, {}, clear=False)
    def test_default_is_agent_root_data(self):
        os.environ.pop("SWARPIUS_DATA_DIR", None)
        self.assertEqual(data_dir(), AGENT_ROOT / "data")

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/mnt/persistent/swarpius"})
    def test_absolute_override(self):
        self.assertEqual(data_dir(), Path("/mnt/persistent/swarpius"))

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "custom_data"})
    def test_relative_override_is_relative_to_agent_root(self):
        self.assertEqual(data_dir(), AGENT_ROOT / "custom_data")


class TestSubdirectories(unittest.TestCase):
    """Subdirectory helpers return paths under data_dir()."""

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"})
    def test_config_dir(self):
        self.assertEqual(config_dir(), Path("/tmp/test-swarpius/config"))

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"})
    def test_conversation_logs_dir(self):
        self.assertEqual(
            conversation_logs_dir(), Path("/tmp/test-swarpius/logs/conversation")
        )

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"})
    def test_server_logs_dir(self):
        self.assertEqual(server_logs_dir(), Path("/tmp/test-swarpius/logs/server"))

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"})
    def test_analysis_dir(self):
        self.assertEqual(analysis_dir(), Path("/tmp/test-swarpius/analysis"))

    @patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"})
    def test_messages_db_path(self):
        self.assertEqual(messages_db_path(), Path("/tmp/test-swarpius/messages.db"))


class TestBundledDataDir(unittest.TestCase):
    """When running as a PyInstaller bundle (sys.frozen + sys._MEIPASS),
    data_dir() returns the per-platform user-data location rather than
    AGENT_ROOT/data (which would be the read-only extraction dir)."""

    def _patch_frozen(self):
        return patch.multiple(sys, frozen=True, _MEIPASS="/fake/meipass", create=True)

    def test_bundled_default_overrides_agent_root_on_linux(self):
        with self._patch_frozen(), patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"HOME": "/home/u", "XDG_DATA_HOME": ""}, clear=False):
            os.environ.pop("SWARPIUS_DATA_DIR", None)
            os.environ.pop("XDG_DATA_HOME", None)
            self.assertEqual(data_dir(), Path("/home/u/.local/share/swarpius"))

    def test_bundled_default_honours_xdg_data_home(self):
        with self._patch_frozen(), patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"XDG_DATA_HOME": "/custom/xdg"}, clear=False):
            os.environ.pop("SWARPIUS_DATA_DIR", None)
            self.assertEqual(data_dir(), Path("/custom/xdg/swarpius"))

    def test_bundled_default_on_macos(self):
        with self._patch_frozen(), patch.object(sys, "platform", "darwin"), \
             patch.dict(os.environ, {"HOME": "/Users/u"}, clear=False):
            os.environ.pop("SWARPIUS_DATA_DIR", None)
            self.assertEqual(
                data_dir(),
                Path("/Users/u/Library/Application Support/Swarpius"),
            )

    def test_bundled_default_on_windows(self):
        with self._patch_frozen(), patch.object(sys, "platform", "win32"), \
             patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/u/AppData/Local"}, clear=False):
            os.environ.pop("SWARPIUS_DATA_DIR", None)
            self.assertEqual(data_dir(), Path("C:/Users/u/AppData/Local/Swarpius"))

    def test_explicit_override_wins_over_bundle_default(self):
        with self._patch_frozen(), \
             patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/explicit"}, clear=False):
            self.assertEqual(data_dir(), Path("/explicit"))

class TestEnsureDirs(unittest.TestCase):
    """ensure_dirs() creates all required directories."""

    def test_creates_all_directories(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                ensure_dirs()

                self.assertTrue((Path(tmp) / "config").is_dir())
                self.assertTrue((Path(tmp) / "logs" / "conversation").is_dir())
                self.assertTrue((Path(tmp) / "logs" / "server").is_dir())
                self.assertTrue((Path(tmp) / "analysis").is_dir())

    def test_idempotent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                ensure_dirs()
                ensure_dirs()  # should not raise
                self.assertTrue((Path(tmp) / "config").is_dir())


class TestStopMarkerAsset(unittest.TestCase):
    """On a bundle launch the packaged stop-marker folder is copied into
    the staging dir under the data dir, so first-run users have the
    silent track locally (to drop into a Roon-watched folder) without
    fetching it from the source repo. The copyable folder sits inside a
    'Stop Simulation' wrapper (so the button can open it without exposing
    the raw data dir). N/A in source mode (the repo copy is canonical)
    and an existing destination is never overwritten."""

    ASSET = "Swarpius Stop Simulation"

    def _patch_frozen(self):
        return patch.multiple(sys, frozen=True, _MEIPASS="/fake/meipass", create=True)

    def test_staging_dir_is_under_data_dir(self):
        from app.data_paths import stop_marker_staging_dir
        with patch.dict(os.environ, {"SWARPIUS_DATA_DIR": "/tmp/test-swarpius"}):
            self.assertEqual(
                stop_marker_staging_dir(), Path("/tmp/test-swarpius/Stop Simulation"),
            )

    def test_noop_in_source_mode(self):
        import tempfile

        from app import data_paths
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                data_paths.ensure_stop_marker_asset()
                self.assertFalse((Path(tmp) / "Stop Simulation").exists())

    def test_copies_packaged_folder_on_bundle_when_absent(self):
        import tempfile

        from app import data_paths
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_data:
            src = Path(tmp_src) / self.ASSET
            src.mkdir()
            (src / "Swarpius_Stop_Playback.wav").write_bytes(b"RIFF")
            with self._patch_frozen(), \
                 patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp_data}), \
                 patch.object(data_paths, "_stop_marker_asset_source", return_value=src):
                data_paths.ensure_stop_marker_asset()
            dest = (
                Path(tmp_data) / "Stop Simulation" / self.ASSET
                / "Swarpius_Stop_Playback.wav"
            )
            self.assertTrue(dest.is_file())

    def test_does_not_clobber_existing_destination(self):
        import tempfile

        from app import data_paths
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_data:
            src = Path(tmp_src) / self.ASSET
            src.mkdir()
            (src / "Swarpius_Stop_Playback.wav").write_bytes(b"NEW")
            dest_dir = Path(tmp_data) / "Stop Simulation" / self.ASSET
            dest_dir.mkdir(parents=True)
            sentinel = dest_dir / "Swarpius_Stop_Playback.wav"
            sentinel.write_bytes(b"OLD")
            with self._patch_frozen(), \
                 patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp_data}), \
                 patch.object(data_paths, "_stop_marker_asset_source", return_value=src):
                data_paths.ensure_stop_marker_asset()
            self.assertEqual(sentinel.read_bytes(), b"OLD")


class TestShouldAutoOpenBrowser(unittest.TestCase):
    """The bundle auto-opens the browser only on a cold start. Source
    mode never does (dev runs the Vite server), and a restart respawn
    doesn't either — the already-open tab reconnects on its own, so a
    fresh tab would just pile up."""

    def _patch_frozen(self):
        return patch.multiple(sys, frozen=True, _MEIPASS="/fake/meipass", create=True)

    def test_source_mode_does_not_open(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RESTART_RESPAWN_ENV, None)
            self.assertFalse(should_auto_open_browser())

    def test_source_mode_does_not_open_even_with_respawn_flag(self):
        with patch.dict(os.environ, {RESTART_RESPAWN_ENV: "1"}, clear=False):
            self.assertFalse(should_auto_open_browser())

    def test_cold_bundle_start_opens(self):
        with self._patch_frozen(), patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RESTART_RESPAWN_ENV, None)
            self.assertTrue(should_auto_open_browser())

    def test_bundle_restart_respawn_does_not_open(self):
        with self._patch_frozen(), \
             patch.dict(os.environ, {RESTART_RESPAWN_ENV: "1"}, clear=False):
            self.assertFalse(should_auto_open_browser())
