"""Readline-based command history for ``agent.py`` (CLI mode).

Two helpers — ``load_history`` on entry and ``save_history`` on exit —
each safe to call when ``readline`` isn't importable (Windows without
``pyreadline3``) or when the history file doesn't yet exist (first
run).
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_MAX_ENTRIES = 1000


def load_history(path: Path) -> None:
    # Best-effort load. A late-delivered Ctrl+C during startup can
    # land inside ``import readline`` itself (Python imports are
    # interruptible), so KeyboardInterrupt is caught alongside the
    # missing-file / corrupt-file paths. ImportError covers Windows
    # without ``pyreadline3``.
    try:
        import readline
        readline.read_history_file(str(path))
    except (ImportError, FileNotFoundError, OSError, KeyboardInterrupt):
        # Swallow: history load is best-effort and must never block CLI startup.
        pass


def save_history(path: Path, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
    # Best-effort save called from ``run_cli_loop``'s ``finally`` —
    # a late Ctrl+C (the user mashing the key during shutdown) can
    # land mid-import or mid-write, and history persistence must not
    # block program exit. OSError covers disk-full / permission /
    # read-only-fs cases; ImportError covers missing readline.
    try:
        import readline
        path.parent.mkdir(parents=True, exist_ok=True)
        readline.set_history_length(max_entries)
        readline.write_history_file(str(path))
    except (ImportError, OSError, KeyboardInterrupt):
        # Swallow: history save is best-effort and must never block program exit.
        pass
