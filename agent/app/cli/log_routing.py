"""Route logs to file (and optionally silence the terminal).

Two related helpers:

* :func:`ensure_default_log_file` — attach a file handler at the default
  path if no file logging is already configured. Used by WS source /
  Docker modes where the operator wants startup logs visible on stderr
  AND a persistent file for post-mortem.

* :func:`route_info_logs_to_file` — the above + silence the stderr
  handler. Used by the interactive CLI (clean ``>>`` prompt — an async
  stderr write would disrupt the visible prompt while readline waits
  for input) and by the installer bundle (the bundle's console is
  end-user-facing, not a developer log).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.runtime.log_format import make_file_handler, silence_console_stderr


def ensure_default_log_file(default_log_path: Path) -> Path:
    """Attach a RotatingFileHandler at the default path if none exists.

    Idempotent across repeat calls and respectful of an existing
    ``LOG_FILE``-configured handler (returns that path instead). Does
    not touch the stderr handler.
    """
    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, RotatingFileHandler)
    ]
    if file_handlers:
        # User already configured ``LOG_FILE`` — respect their choice.
        return Path(file_handlers[0].baseFilename)

    default_log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = make_file_handler(default_log_path)
    root.addHandler(handler)
    return default_log_path


def route_info_logs_to_file(default_log_path: Path) -> Path:
    """Silence stderr and ensure a file handler at INFO.

    Returns the active log path so callers can surface it in a banner.
    User-facing notices in CLI / bundle mode must use Rich explicitly —
    the logger is no longer a console channel here.
    """
    silence_console_stderr()
    return ensure_default_log_file(default_log_path)
