"""``route_info_logs_to_file`` reshapes the root logger for CLI
mode and the installer bundle's user-facing console:

  * The existing stderr handler (added by ``logging.basicConfig``
    at module import) is **removed** — async log writes to stderr
    would overwrite the ``>>`` prompt visually while ``input()``
    is waiting, leaving the user staring at a blank line. The
    file handler keeps the full record for post-mortem.
  * A RotatingFileHandler at INFO is ensured so the full debug
    stream is preserved somewhere; if the operator hasn't set
    ``LOG_FILE``, we default to ``<data_dir>/logs/swarpius.log``
    so there's always a paper trail.
  * Returns the active log path so the banner can show it.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.cli.log_routing import ensure_default_log_file, route_info_logs_to_file


class _LoggerFixture:
    """Save / restore root logger handlers so tests don't pollute
    each other."""

    def __init__(self):
        self._saved_handlers: list = []
        self._saved_level: int = logging.NOTSET

    def __enter__(self):
        root = logging.getLogger()
        self._saved_handlers = list(root.handlers)
        self._saved_level = root.level
        root.handlers = []
        return self

    def __exit__(self, *exc):
        root = logging.getLogger()
        for h in root.handlers:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
        root.handlers = list(self._saved_handlers)
        root.setLevel(self._saved_level)


class TestRouteInfoLogsToFile(unittest.TestCase):
    def test_stderr_handler_removed(self) -> None:
        # Async log writes to stderr disrupt the interactive ``>>``
        # prompt visually; the contract is that route_info_logs_to_file
        # leaves NO stderr StreamHandler on the root logger.
        with _LoggerFixture():
            root = logging.getLogger()
            stderr_handler = logging.StreamHandler()
            stderr_handler.setLevel(logging.INFO)
            root.addHandler(stderr_handler)

            with tempfile.TemporaryDirectory() as td:
                route_info_logs_to_file(default_log_path=Path(td) / "swarpius.log")

            remaining_stream_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, RotatingFileHandler)
            ]
            self.assertEqual(remaining_stream_handlers, [])

    def test_file_handler_added_at_default_path(self) -> None:
        with _LoggerFixture():
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / "logs" / "swarpius.log"
                returned = route_info_logs_to_file(default_log_path=target)
                # Assert inside the tempdir context — once it exits
                # the directory is removed and the assertion would
                # spuriously fail.
                file_handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                self.assertEqual(len(file_handlers), 1)
                self.assertEqual(file_handlers[0].level, logging.INFO)
                self.assertEqual(returned, target)
                self.assertTrue(target.parent.exists())

    def test_existing_file_handler_preserved(self) -> None:
        """If LOG_FILE was set at module import, the existing
        handler stays — we don't add a second one."""
        with _LoggerFixture():
            with tempfile.TemporaryDirectory() as td:
                user_log = Path(td) / "user_chosen.log"
                user_handler = RotatingFileHandler(user_log, maxBytes=1024, backupCount=1)
                user_handler.setLevel(logging.INFO)
                logging.getLogger().addHandler(user_handler)

                returned = route_info_logs_to_file(
                    default_log_path=Path(td) / "default" / "swarpius.log",
                )

                file_handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                # Still just the one handler — the user's.
                self.assertEqual(len(file_handlers), 1)
                self.assertEqual(returned, user_log)


class TestEnsureDefaultLogFile(unittest.TestCase):
    """``ensure_default_log_file`` is the file-only half, used by WS
    source / Docker where operators want stderr kept as well."""

    def test_attaches_handler_at_default_path(self) -> None:
        with _LoggerFixture():
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / "logs" / "swarpius.log"
                returned = ensure_default_log_file(default_log_path=target)
                file_handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                self.assertEqual(len(file_handlers), 1)
                self.assertEqual(file_handlers[0].level, logging.INFO)
                self.assertEqual(returned, target)
                self.assertTrue(target.parent.exists())

    def test_does_not_silence_stderr(self) -> None:
        """Distinguishes from route_info_logs_to_file: operators in
        WS source / Docker mode want startup logs visible on stderr
        AND persisted to file."""
        with _LoggerFixture():
            root = logging.getLogger()
            stderr_handler = logging.StreamHandler()
            stderr_handler.setLevel(logging.INFO)
            root.addHandler(stderr_handler)

            with tempfile.TemporaryDirectory() as td:
                ensure_default_log_file(default_log_path=Path(td) / "swarpius.log")

            remaining_stream_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, RotatingFileHandler)
            ]
            self.assertEqual(len(remaining_stream_handlers), 1)
            self.assertIs(remaining_stream_handlers[0], stderr_handler)

    def test_respects_existing_file_handler(self) -> None:
        with _LoggerFixture():
            with tempfile.TemporaryDirectory() as td:
                user_log = Path(td) / "user_chosen.log"
                user_handler = RotatingFileHandler(user_log, maxBytes=1024, backupCount=1)
                user_handler.setLevel(logging.INFO)
                logging.getLogger().addHandler(user_handler)

                returned = ensure_default_log_file(
                    default_log_path=Path(td) / "default" / "swarpius.log",
                )

                file_handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                self.assertEqual(len(file_handlers), 1)
                self.assertEqual(returned, user_log)


if __name__ == "__main__":
    unittest.main()
