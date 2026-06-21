"""Contract tests for the ``swarpius`` supervisor's restart loop.

The supervisor's job is to invoke the agent, restart it when the agent
requests one (specific exit code), pass through any other exit code,
and refuse to spin forever if the agent keeps asking to restart in
quick succession. Tests inject a fake agent runner so the behaviour is
exercised without spawning real subprocesses.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import swarpius
from app.data_paths import RESTART_RESPAWN_ENV
from swarpius import (
    _SUPERVISED_CHILD_ENV,
    RESTART_EXIT_CODE,
    _run_agent_subprocess,
    _supervised_agent_runner,
    _wait_for_child,
    main,
    run_supervised,
)


class TestExitCodePassthrough(unittest.TestCase):
    def test_clean_exit_returns_zero(self):
        result = run_supervised(lambda argv: 0, [])
        self.assertEqual(result, 0)

    def test_arbitrary_non_restart_exit_passes_through(self):
        result = run_supervised(lambda argv: 42, [])
        self.assertEqual(result, 42)

    def test_signal_exit_code_passes_through(self):
        # 130 = SIGINT on POSIX. Anything that isn't the restart sentinel
        # is the agent's final word; the supervisor doesn't override.
        result = run_supervised(lambda argv: 130, [])
        self.assertEqual(result, 130)


class TestRestartLoop(unittest.TestCase):
    def test_restart_exit_triggers_respawn(self):
        results = iter([RESTART_EXIT_CODE, 0])
        result = run_supervised(lambda argv: next(results), [])
        self.assertEqual(result, 0)

    def test_multiple_restarts_followed_by_clean_exit(self):
        results = iter([RESTART_EXIT_CODE, RESTART_EXIT_CODE, RESTART_EXIT_CODE, 0])
        result = run_supervised(lambda argv: next(results), [])
        self.assertEqual(result, 0)

    def test_restart_then_non_zero_exit_passes_that_code_through(self):
        results = iter([RESTART_EXIT_CODE, 7])
        result = run_supervised(lambda argv: next(results), [])
        self.assertEqual(result, 7)


class TestArgvForwarding(unittest.TestCase):
    def test_argv_forwarded_to_agent(self):
        seen: list[list[str]] = []

        def runner(argv: list[str]) -> int:
            seen.append(list(argv))
            return 0

        run_supervised(runner, ["--ws", "--show-request-ids"])
        self.assertEqual(seen, [["--ws", "--show-request-ids"]])

    def test_same_argv_forwarded_across_restarts(self):
        seen: list[list[str]] = []
        results = iter([RESTART_EXIT_CODE, RESTART_EXIT_CODE, 0])

        def runner(argv: list[str]) -> int:
            seen.append(list(argv))
            return next(results)

        run_supervised(runner, ["--ws"])
        self.assertEqual(seen, [["--ws"], ["--ws"], ["--ws"]])

    def test_empty_argv_forwards_empty_list(self):
        seen: list[list[str]] = []

        def runner(argv: list[str]) -> int:
            seen.append(list(argv))
            return 0

        run_supervised(runner, [])
        self.assertEqual(seen, [[]])


class TestCrashLoopGuard(unittest.TestCase):
    """If the agent requests more than ``threshold`` restarts within
    ``window`` seconds, the supervisor gives up. Tests use injectable
    ``now_func`` so they don't depend on wall-clock time."""

    def test_too_many_restarts_inside_window_gives_up(self):
        result = run_supervised(
            lambda argv: RESTART_EXIT_CODE,
            [],
            crash_loop_threshold=3,
            crash_loop_window_seconds=60.0,
            now_func=lambda: 0.0,
        )
        # Crash-loop give-up uses a non-zero exit code so a calling
        # wrapper (docker compose, .bat file) can detect the failure.
        self.assertNotEqual(result, 0)

    def test_restarts_spread_outside_window_do_not_trip(self):
        # Five restarts then exit — but each separated by 100s, so the
        # 30s window only ever sees one at a time. Guard never trips.
        times = iter([0.0, 100.0, 200.0, 300.0, 400.0, 500.0])
        results = iter([RESTART_EXIT_CODE] * 4 + [0])
        result = run_supervised(
            lambda argv: next(results),
            [],
            crash_loop_threshold=3,
            crash_loop_window_seconds=30.0,
            now_func=lambda: next(times),
        )
        self.assertEqual(result, 0)

    def test_threshold_is_inclusive_boundary(self):
        # threshold=3 means three restarts are allowed; the FOURTH
        # consecutive restart inside the window trips the guard.
        # Restart counts: 1, 2, 3 (all ok), 4 trips → give up.
        seen: list[int] = []

        def runner(argv: list[str]) -> int:
            seen.append(1)
            return RESTART_EXIT_CODE

        result = run_supervised(
            runner,
            [],
            crash_loop_threshold=3,
            crash_loop_window_seconds=60.0,
            now_func=lambda: 0.0,
        )
        # 4 invocations: the first three each returned 75 + were
        # respawned; the 4th return-75 trips the guard.
        self.assertEqual(len(seen), 4)
        self.assertNotEqual(result, 0)


class TestWaitForChild(unittest.TestCase):
    """``_wait_for_child`` swallows ``KeyboardInterrupt`` while
    waiting on the child process. SIGINT in a terminal goes to the
    whole foreground process group, so the child receives the same
    signal and runs its own shutdown sequence. The supervisor's only
    job is to keep waiting until the child exits."""

    def test_returns_exit_code_on_clean_exit(self) -> None:
        proc = MagicMock()
        proc.wait.return_value = 0
        self.assertEqual(_wait_for_child(proc), 0)

    def test_passes_through_arbitrary_exit_code(self) -> None:
        proc = MagicMock()
        proc.wait.return_value = 42
        self.assertEqual(_wait_for_child(proc), 42)

    def test_keyboard_interrupt_is_swallowed_then_we_keep_waiting(self) -> None:
        """The bug this fixes: a user pressing Ctrl+C twice to exit
        the agent (1st arms, 2nd triggers) also delivered SIGINT to
        the supervisor, whose unguarded ``proc.wait(timeout=10)``
        raised ``KeyboardInterrupt`` and dumped a traceback after
        the child had already exited cleanly."""
        proc = MagicMock()
        proc.wait.side_effect = [KeyboardInterrupt, 0]
        self.assertEqual(_wait_for_child(proc), 0)
        self.assertEqual(proc.wait.call_count, 2)

    def test_multiple_keyboard_interrupts_are_all_swallowed(self) -> None:
        """The user might mash Ctrl+C while the child is taking a
        moment to wind down — each interrupt just sends us back to
        wait()."""
        proc = MagicMock()
        proc.wait.side_effect = [
            KeyboardInterrupt,
            KeyboardInterrupt,
            KeyboardInterrupt,
            7,
        ]
        self.assertEqual(_wait_for_child(proc), 7)
        self.assertEqual(proc.wait.call_count, 4)


class TestAgentSpawn(unittest.TestCase):
    def test_source_mode_spawns_agent_script(self) -> None:
        with patch.object(swarpius, "_running_from_bundle", return_value=False), \
             patch.object(swarpius.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            _run_agent_subprocess(["--ws"])
            cmd = popen.call_args.args[0]
            self.assertEqual(cmd[0], sys.executable)
            self.assertTrue(str(cmd[1]).endswith("agent.py"))
            self.assertEqual(list(cmd[2:]), ["--ws"])
            self.assertNotIn(_SUPERVISED_CHILD_ENV, popen.call_args.kwargs.get("env") or {})

    def test_frozen_mode_relaunches_exe_with_child_sentinel(self) -> None:
        with patch.object(swarpius, "_running_from_bundle", return_value=True), \
             patch.object(swarpius.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            _run_agent_subprocess(["--ws"])
            cmd = popen.call_args.args[0]
            self.assertEqual(list(cmd), [sys.executable, "--ws"])
            self.assertEqual(popen.call_args.kwargs["env"].get(_SUPERVISED_CHILD_ENV), "1")


class TestFrozenChildEntry(unittest.TestCase):
    def test_child_sentinel_runs_agent_main_not_supervisor(self) -> None:
        fake_agent = MagicMock()
        with patch.object(swarpius, "_running_from_bundle", return_value=True), \
             patch.dict(os.environ, {_SUPERVISED_CHILD_ENV: "1"}), \
             patch.dict(sys.modules, {"agent": fake_agent}), \
             patch.object(swarpius, "run_supervised") as run_sup:
            result = main()
            fake_agent.main.assert_called_once()
            run_sup.assert_not_called()
            self.assertEqual(result, 0)

    def test_frozen_without_sentinel_supervises(self) -> None:
        env_without = {k: v for k, v in os.environ.items() if k != _SUPERVISED_CHILD_ENV}
        with patch.object(swarpius, "_running_from_bundle", return_value=True), \
             patch.dict(os.environ, env_without, clear=True), \
             patch.object(swarpius, "run_supervised", return_value=0) as run_sup:
            result = main()
            run_sup.assert_called_once()
            self.assertEqual(result, 0)


class TestRestartRespawnSignal(unittest.TestCase):
    """The supervisor flags every respawn after the first launch with an
    env sentinel so the bundle child can tell a Restart from a
    cold start and skip reopening the browser. The first spawn carries no
    sentinel; every respawn does."""

    def test_cold_spawn_omits_restart_sentinel(self) -> None:
        with patch.object(swarpius, "_running_from_bundle", return_value=True), \
             patch.object(swarpius.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            _run_agent_subprocess(["--ws"], is_restart=False)
            self.assertNotIn(RESTART_RESPAWN_ENV, popen.call_args.kwargs["env"])

    def test_restart_spawn_sets_sentinel(self) -> None:
        with patch.object(swarpius, "_running_from_bundle", return_value=True), \
             patch.object(swarpius.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            _run_agent_subprocess(["--ws"], is_restart=True)
            self.assertEqual(popen.call_args.kwargs["env"].get(RESTART_RESPAWN_ENV), "1")

    def test_supervised_runner_marks_only_respawns(self) -> None:
        # Drives the real runner + _run_agent_subprocess + Popen wiring:
        # exit-75 then clean exit means two spawns — first cold, second a
        # restart respawn.
        with patch.object(swarpius, "_running_from_bundle", return_value=True), \
             patch.object(swarpius.subprocess, "Popen") as popen:
            popen.return_value.wait.side_effect = [RESTART_EXIT_CODE, 0]
            run_supervised(_supervised_agent_runner(), ["--ws"])
            self.assertEqual(popen.call_count, 2)
            self.assertNotIn(
                RESTART_RESPAWN_ENV, popen.call_args_list[0].kwargs["env"],
            )
            self.assertEqual(
                popen.call_args_list[1].kwargs["env"].get(RESTART_RESPAWN_ENV), "1",
            )


if __name__ == "__main__":
    unittest.main()
