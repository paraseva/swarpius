"""Swarpius supervisor — entry point that spawns and restarts ``agent.py``.

The agent itself is ``agent.py``. The supervisor exists because Windows
has no true ``exec()``: an in-place restart on Windows is implemented as
``CreateProcess + ExitProcess``, which orphans the new agent from its
parent shell — Ctrl+C no longer routes, the .exe leaves a port-hog if
its own ``AutoShutdown`` ever fails, and end users have to use Task
Manager to clean up. The supervisor solves this by staying the
foreground / parent process across restarts. The agent's ``Apply &
Restart`` flow now just exits with code ``75``; the supervisor sees
that and respawns.

Used as the entry for every native invocation (CLI and WS) and the
installer/.exe. Docker bypasses it (compose's restart policy plays
the supervisor role and the container's lifecycle gates the agent's).

The restart-loop logic — ``run_supervised`` — is decoupled from
subprocess spawning so it can be tested without launching real
processes. ``_run_agent_subprocess`` is the production runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, List

# Exit code the agent uses to signal "please restart me". Any other
# exit code (0, 1, 130, …) is the agent's final word; the supervisor
# returns it unchanged.
RESTART_EXIT_CODE = 75

# More than ``CRASH_LOOP_THRESHOLD`` restarts within
# ``CRASH_LOOP_WINDOW_SECONDS`` exits with a non-zero status. Stops
# an agent that exits-75 in a tight loop from spinning forever and
# burning CPU; surfaces the failure to a calling wrapper (compose,
# .bat launcher) so it can apply its own backoff or alert.
CRASH_LOOP_THRESHOLD = 5
CRASH_LOOP_WINDOW_SECONDS = 30.0

_AGENT_SCRIPT = Path(__file__).parent / "agent.py"

# A frozen bundle has no separate agent.py and sys.executable is the bundle
# exe, so the supervisor re-launches the exe with this sentinel set; the child
# then runs the agent. os.execv can't be used — on Windows it detaches the
# child from the parent.
_SUPERVISED_CHILD_ENV = "SWARPIUS_SUPERVISED_CHILD"


def _running_from_bundle() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _wait_for_child(proc: Any) -> int:
    """Wait for ``proc`` to exit and return its exit code, swallowing
    any ``KeyboardInterrupt`` along the way.

    SIGINT in a terminal goes to the whole foreground process group,
    so the child receives the same Ctrl+C the supervisor sees. The
    child runs its own shutdown sequence; the supervisor's only job
    is to keep waiting until that's done. Without this guard,
    ``proc.wait`` raises ``KeyboardInterrupt`` in the supervisor and
    leaves a traceback on the user's screen after a clean child exit.
    """
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            pass


def _run_agent_subprocess(argv: List[str], *, is_restart: bool = False) -> int:
    """Spawn the agent as a child process, wait, return its exit code.

    ``is_restart`` marks a respawn after a Restart (vs the first
    launch) so the bundle child can skip reopening the browser.
    """
    if _running_from_bundle():
        from app.data_paths import RESTART_RESPAWN_ENV

        env = {**os.environ, _SUPERVISED_CHILD_ENV: "1"}
        if is_restart:
            env[RESTART_RESPAWN_ENV] = "1"
        proc = subprocess.Popen([sys.executable, *argv], env=env)
    else:
        proc = subprocess.Popen([sys.executable, str(_AGENT_SCRIPT), *argv])
    return _wait_for_child(proc)


def _supervised_agent_runner() -> Callable[[List[str]], int]:
    """Production runner for ``run_supervised``.

    ``run_supervised`` only re-invokes the runner after a restart exit, so
    every call after the first is a respawn — flag those so the child
    knows it's a restart rather than a cold start.
    """
    launched = False

    def run(argv: List[str]) -> int:
        nonlocal launched
        is_restart = launched
        launched = True
        return _run_agent_subprocess(argv, is_restart=is_restart)

    return run


def run_supervised(
    agent_runner: Callable[[List[str]], int],
    argv: List[str],
    *,
    restart_exit_code: int = RESTART_EXIT_CODE,
    crash_loop_threshold: int = CRASH_LOOP_THRESHOLD,
    crash_loop_window_seconds: float = CRASH_LOOP_WINDOW_SECONDS,
    now_func: Callable[[], float] = time.monotonic,
) -> int:
    """Invoke the agent, restart on ``restart_exit_code``, return the
    final exit code.

    ``agent_runner`` is the thing that actually runs the agent (a real
    subprocess in production, a stub in tests). Injecting it lets the
    supervision logic be tested without spawning processes.
    """
    restart_timestamps: List[float] = []
    while True:
        exit_code = agent_runner(argv)
        if exit_code != restart_exit_code:
            return exit_code

        now = now_func()
        restart_timestamps.append(now)
        restart_timestamps = [
            t for t in restart_timestamps if now - t <= crash_loop_window_seconds
        ]
        if len(restart_timestamps) > crash_loop_threshold:
            sys.stderr.write(
                f"swarpius supervisor: agent requested restart "
                f"{len(restart_timestamps)} times in "
                f"{crash_loop_window_seconds:.0f}s; giving up.\n",
            )
            return 1


def main() -> int:
    if _running_from_bundle() and os.environ.get(_SUPERVISED_CHILD_ENV):
        import agent

        agent.main()
        return 0
    return run_supervised(_supervised_agent_runner(), sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
