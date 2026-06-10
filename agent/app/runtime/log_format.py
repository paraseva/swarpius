"""Single source of truth for log format + the two reusable handler
helpers (file + stderr quietening). Imported by swarpius.py module
init and by app/cli/log_routing.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 28-char source column keeps every current ``swarpius.*`` logger
# name aligned; a slightly longer name just wobbles for one line.
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)-28s %(message)s"

# Sortable ISO-8601 with local time. No 'Z' — that would imply UTC,
# which the asctime is not. Sortable lexicographically.
LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"

_FILE_MAX_BYTES = 10 * 1024 * 1024
_FILE_BACKUP_COUNT = 3


def make_file_handler(path: Path) -> RotatingFileHandler:
    """Rotating file handler at INFO with the project's standard format."""
    handler = RotatingFileHandler(
        path,
        maxBytes=_FILE_MAX_BYTES,
        backupCount=_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    handler.setLevel(logging.INFO)
    return handler


def add_file_handler_once(path: Path) -> bool:
    """Attach a file handler at ``path`` to the root logger unless one
    for the same resolved path is already attached. Returns True if a
    handler was added. Guards against double-logging when more than one
    startup path tries to configure the same file."""
    target = str(Path(path).resolve())
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and str(Path(h.baseFilename).resolve()) == target:
            return False
    root.addHandler(make_file_handler(path))
    return True


class UnclosedSessionFilter(logging.Filter):
    """Drops aiohttp's "Unclosed client session / connector" warnings.

    These are emitted by asyncio during garbage collection when a
    LiteLLM call leaves a session unclosed (the analyser's batch LLM
    calls do this). The sessions are still freed; the warnings are
    pure noise that otherwise floods the log. Real asyncio errors
    (task destruction, etc.) pass through untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            message.startswith("Unclosed client session")
            or message.startswith("Unclosed connector")
        )


def quiet_console_stderr() -> None:
    """Bump the root logger's stderr handler(s) to WARNING. Used when
    logs are also being written to a file — a noisy stderr would
    duplicate what's in the file and clutter the user's terminal."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            h.setLevel(logging.WARNING)


def silence_console_stderr() -> None:
    """Remove the root logger's stderr handler(s) entirely. Used in
    CLI / installer-bundle modes where ``input()`` runs on the same
    terminal as log output. An async stderr write between
    prompt-display and user keystroke would overwrite the ``>>``
    prompt visually (readline buffering keeps keystrokes correct,
    but the user sees a blank line). The file handler is left
    untouched so the full record survives for post-mortem; any
    user-facing notice must route through Rich explicitly."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            root.removeHandler(h)
