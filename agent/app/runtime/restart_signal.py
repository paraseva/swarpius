"""Module-level flag for save-and-restart.

The Settings UI's "Restart" sets the flag via
:func:`request_restart` so that after the WS server shuts down
cleanly, the main block reads it and calls :func:`perform_restart`,
which exits with code ``75``. The ``swarpius`` supervisor process
sees that code and respawns the agent — this is how restart-in-place
works uniformly across native CLI, native WS, and the installer .exe.

Docker compose doesn't run the supervisor; the container's restart
policy handles respawn, so the Docker path in ``agent.py`` exits
zero and lets compose do its job.
"""
from __future__ import annotations

import sys
import threading

# Exit code the supervisor recognises as "please respawn me". Kept
# in sync with ``swarpius.RESTART_EXIT_CODE``.
RESTART_EXIT_CODE = 75

_lock = threading.Lock()
_requested = False


def request_restart() -> None:
    """Mark that a restart was intentionally requested."""
    global _requested
    with _lock:
        _requested = True


def is_restart_requested() -> bool:
    """Check whether ``request_restart()`` was called this session."""
    with _lock:
        return _requested


def clear() -> None:
    """Reset the flag (used by tests)."""
    global _requested
    with _lock:
        _requested = False


def perform_restart() -> None:
    """Exit with the restart sentinel exit code.

    The ``swarpius`` supervisor catches this exit code and respawns
    the agent. Callers are responsible for gating on
    :func:`is_restart_requested` and ensuring open sockets / logs have
    been flushed before this is invoked; once we exit, the OS reclaims
    file descriptors and the supervisor starts a fresh process from
    scratch.
    """
    sys.exit(RESTART_EXIT_CODE)
