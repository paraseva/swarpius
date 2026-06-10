"""CLI readline history loading + saving.

Pins the contract that:
  * ``load_history`` silently returns when the history file doesn't
    exist (first-run case) — readline raises FileNotFoundError on a
    missing file, which would otherwise crash the CLI startup.
  * ``save_history`` creates the parent directory if it doesn't exist
    yet (data dir may be brand new on first run).
  * ``save_history`` truncates to ``max_entries`` so a long-lived
    interactive session can't grow the history file unboundedly.
  * Both helpers degrade gracefully when the ``readline`` module is
    unavailable (Windows without ``pyreadline3``).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.cli import history as cli_history  # noqa: E402
from app.data_paths import cli_history_path, data_dir  # noqa: E402


class TestCliHistoryPath(unittest.TestCase):
    def test_path_is_under_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"SWARPIUS_DATA_DIR": td}):
                path = cli_history_path()
                self.assertEqual(path, data_dir() / "cli_history")
                self.assertTrue(str(path).startswith(td))


class TestLoadHistory(unittest.TestCase):
    def test_calls_readline_with_path_string(self) -> None:
        fake_readline = MagicMock()
        with patch.dict(sys.modules, {"readline": fake_readline}):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "cli_history"
                p.write_text("show me elvis\n")
                cli_history.load_history(p)
        fake_readline.read_history_file.assert_called_once_with(str(p))

    def test_silent_when_file_missing(self) -> None:
        """First-run case: no history file yet. Readline raises
        FileNotFoundError, which we must swallow."""
        fake_readline = MagicMock()
        fake_readline.read_history_file.side_effect = FileNotFoundError
        with patch.dict(sys.modules, {"readline": fake_readline}):
            cli_history.load_history(Path("/nonexistent/cli_history"))

    def test_silent_when_readline_unavailable(self) -> None:
        """Windows without pyreadline3 has no readline module."""
        sentinel = sys.modules.pop("readline", None)
        try:
            with patch.dict(
                sys.modules, {"readline": None},  # forces ImportError
            ):
                cli_history.load_history(Path("/whatever"))
        finally:
            if sentinel is not None:
                sys.modules["readline"] = sentinel


class TestSaveHistory(unittest.TestCase):
    def test_writes_history_with_length_cap(self) -> None:
        fake_readline = MagicMock()
        with patch.dict(sys.modules, {"readline": fake_readline}):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "cli_history"
                cli_history.save_history(p, max_entries=500)
        fake_readline.set_history_length.assert_called_once_with(500)
        fake_readline.write_history_file.assert_called_once_with(str(p))

    def test_creates_parent_directory(self) -> None:
        """Data dir might not exist yet on first run."""
        fake_readline = MagicMock()
        with patch.dict(sys.modules, {"readline": fake_readline}):
            with tempfile.TemporaryDirectory() as td:
                nested = Path(td) / "fresh" / "data"
                p = nested / "cli_history"
                self.assertFalse(nested.exists())
                cli_history.save_history(p)
                self.assertTrue(nested.is_dir())

    def test_silent_when_readline_unavailable(self) -> None:
        sentinel = sys.modules.pop("readline", None)
        try:
            with patch.dict(sys.modules, {"readline": None}):
                with tempfile.TemporaryDirectory() as td:
                    cli_history.save_history(Path(td) / "cli_history")
        finally:
            if sentinel is not None:
                sys.modules["readline"] = sentinel

    def test_keyboard_interrupt_during_save_swallowed(self) -> None:
        """A late-delivered Ctrl+C during process exit can land
        inside save_history's readline operations. History
        persistence is best-effort cleanup — failure to save
        must not crash the program on the way out."""
        fake_readline = MagicMock()
        fake_readline.write_history_file.side_effect = KeyboardInterrupt
        with patch.dict(sys.modules, {"readline": fake_readline}):
            with tempfile.TemporaryDirectory() as td:
                cli_history.save_history(Path(td) / "cli_history")

    def test_os_error_during_save_swallowed(self) -> None:
        """Disk full / permission denied / read-only filesystem —
        all best-effort failures, never propagate."""
        fake_readline = MagicMock()
        fake_readline.write_history_file.side_effect = OSError("disk full")
        with patch.dict(sys.modules, {"readline": fake_readline}):
            with tempfile.TemporaryDirectory() as td:
                cli_history.save_history(Path(td) / "cli_history")

    def test_keyboard_interrupt_during_import_swallowed(self) -> None:
        """A second Ctrl+C arriving after the CLI exit has begun can
        land DURING ``import readline`` itself (Python imports are
        interruptible). The import sits at the top of save_history;
        if it isn't caught, the program crashes with a traceback on
        the way out."""
        import builtins
        sys.modules.pop("readline", None)
        original_import = builtins.__import__

        def interrupted_import(name, *args, **kwargs):
            if name == "readline":
                raise KeyboardInterrupt
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=interrupted_import):
            with tempfile.TemporaryDirectory() as td:
                cli_history.save_history(Path(td) / "cli_history")


class TestLoadHistoryResilience(unittest.TestCase):
    def test_keyboard_interrupt_during_load_swallowed(self) -> None:
        fake_readline = MagicMock()
        fake_readline.read_history_file.side_effect = KeyboardInterrupt
        with patch.dict(sys.modules, {"readline": fake_readline}):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "cli_history"
                p.write_text("")
                cli_history.load_history(p)

if __name__ == "__main__":
    unittest.main()
