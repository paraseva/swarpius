"""Bundle-mode auto-shutdown state machine.

When the browser disconnects there's a short reconnect grace (rides out
an F5 / brief blip), then the process exits immediately — no drawn-out
countdown that would hold the port and leave the user unsure whether it
has quit.
"""

from __future__ import annotations

import asyncio
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


from app.runtime.auto_shutdown import AutoShutdown


def _run(coro):
    return asyncio.run(coro)


class TestAutoShutdown(unittest.TestCase):

    def _make(self, *, disconnect=0.05, startup=0.05):
        loop = asyncio.get_running_loop()
        calls = []
        helper = AutoShutdown(
            loop,
            lambda: calls.append("shutdown"),
            disconnect_grace_seconds=disconnect,
            startup_grace_seconds=startup,
        )
        return helper, calls

    def test_startup_grace_fires_when_no_one_connects(self):
        async def body():
            helper, calls = self._make(startup=0.02)
            helper.start_startup_grace()
            await asyncio.sleep(0.05)
            self.assertEqual(calls, ["shutdown"])

        _run(body())

    def test_startup_grace_cancelled_by_first_connect(self):
        async def body():
            helper, calls = self._make(startup=0.05)
            helper.start_startup_grace()
            await asyncio.sleep(0.01)
            helper.on_connect()
            await asyncio.sleep(0.06)
            self.assertEqual(calls, [])

        _run(body())

    def test_disconnect_fires_immediately_after_grace(self):
        # The defining contract: once the reconnect grace elapses, shutdown
        # fires right away — no multi-second countdown. grace=0.02, so a
        # fire observed by 0.05 proves nothing lingers after the grace.
        async def body():
            helper, calls = self._make(disconnect=0.02)
            helper.on_connect()
            helper.on_disconnect()
            await asyncio.sleep(0.05)
            self.assertEqual(calls, ["shutdown"])

        _run(body())

    def test_reconnect_in_grace_window_cancels_shutdown(self):
        async def body():
            helper, calls = self._make(disconnect=0.05)
            helper.on_connect()
            helper.on_disconnect()
            await asyncio.sleep(0.01)
            helper.on_connect()
            await asyncio.sleep(0.06)
            self.assertEqual(calls, [])

        _run(body())

    def test_does_not_schedule_when_other_clients_remain(self):
        async def body():
            helper, calls = self._make(disconnect=0.02)
            helper.on_connect()
            helper.on_connect()
            helper.on_disconnect()
            await asyncio.sleep(0.05)
            self.assertEqual(calls, [])

        _run(body())

    def test_spurious_disconnect_at_zero_does_not_schedule(self):
        async def body():
            helper, calls = self._make(disconnect=0.02)
            # No prior connect — must not schedule (trigger is the
            # N→0 transition, not just count==0).
            helper.on_disconnect()
            helper.on_disconnect()
            await asyncio.sleep(0.05)
            self.assertEqual(calls, [])

        _run(body())

    def test_restart_in_progress_suppresses_disconnect_shutdown(self):
        # A restart drops the browser (server closing to respawn); the
        # auto-shutdown must stand down, or it exits 0 and the supervisor
        # (which wants 75) turns the restart into a quit.
        from app.runtime import restart_signal

        restart_signal.request_restart()

        async def body():
            helper, calls = self._make(disconnect=0.02)
            helper.on_connect()
            helper.on_disconnect()
            await asyncio.sleep(0.05)
            self.assertEqual(calls, [])

        _run(body())

    def tearDown(self):
        # The restart flag is module-global; reset it so it can't leak
        # into other tests.
        from app.runtime import restart_signal

        restart_signal.clear()


if __name__ == "__main__":
    unittest.main()
