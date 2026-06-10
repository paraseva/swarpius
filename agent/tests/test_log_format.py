"""``add_file_handler_once`` (idempotent file-handler attach) and
``UnclosedSessionFilter`` (drops aiohttp GC-noise records).

Both exist to keep ``swarpius.log`` clean: the first prevents a second
RotatingFileHandler for the same path from doubling every line; the
second drops the benign "Unclosed client session / connector" warnings
the analyser's LiteLLM calls emit during garbage collection.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.runtime.log_format import (
    UnclosedSessionFilter,
    add_file_handler_once,
)


class _RootFixture:
    def __enter__(self):
        root = logging.getLogger()
        self._saved = list(root.handlers)
        root.handlers = []
        return self

    def __exit__(self, *exc):
        root = logging.getLogger()
        for h in root.handlers:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
        root.handlers = list(self._saved)


class TestAddFileHandlerOnce(unittest.TestCase):
    def test_adds_a_handler_when_none_present(self) -> None:
        with _RootFixture():
            with tempfile.TemporaryDirectory() as td:
                added = add_file_handler_once(Path(td) / "swarpius.log")
                handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                self.assertTrue(added)
                self.assertEqual(len(handlers), 1)

    def test_second_call_same_path_is_noop(self) -> None:
        with _RootFixture():
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / "swarpius.log"
                first = add_file_handler_once(target)
                second = add_file_handler_once(target)
                handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                self.assertTrue(first)
                self.assertFalse(second)
                self.assertEqual(len(handlers), 1)

    def test_resolves_path_so_relative_and_absolute_dedupe(self) -> None:
        with _RootFixture():
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / "swarpius.log"
                add_file_handler_once(target)
                # Same file expressed with a redundant '.' segment.
                second = add_file_handler_once(Path(td) / "." / "swarpius.log")
                handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)
                ]
                self.assertFalse(second)
                self.assertEqual(len(handlers), 1)


class TestUnclosedSessionFilter(unittest.TestCase):
    def _record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="asyncio", level=logging.ERROR, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_drops_unclosed_client_session(self) -> None:
        f = UnclosedSessionFilter()
        self.assertFalse(f.filter(self._record("Unclosed client session")))

    def test_drops_unclosed_connector(self) -> None:
        f = UnclosedSessionFilter()
        self.assertFalse(f.filter(self._record("Unclosed connector")))

    def test_keeps_other_asyncio_errors(self) -> None:
        f = UnclosedSessionFilter()
        self.assertTrue(f.filter(self._record("Task was destroyed but it is pending!")))


if __name__ == "__main__":
    unittest.main()
