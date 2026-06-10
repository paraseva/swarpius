"""Tests for the save-and-restart flag module.

The flow is split across three pieces:
- ``app.restart_signal`` (this module's subject) holds the flag and
  exposes :func:`perform_restart`, which exits with the sentinel code
  the ``swarpius`` supervisor uses to decide "respawn me".
- ``app.websocket_flow`` dispatch sets the flag when ``restart=true``
  in a successful save request.
- ``agent.py`` main block reads the flag after WS shutdown and either
  calls :func:`perform_restart` (native) or exits zero (Docker, where
  compose's restart policy respawns the container).
"""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.runtime import restart_signal


class TestRestartSignal(unittest.TestCase):
    def setUp(self):
        restart_signal.clear()

    def tearDown(self):
        restart_signal.clear()

    def test_default_is_not_requested(self):
        self.assertFalse(restart_signal.is_restart_requested())

    def test_request_sets_flag(self):
        restart_signal.request_restart()
        self.assertTrue(restart_signal.is_restart_requested())

    def test_clear_resets_flag(self):
        restart_signal.request_restart()
        restart_signal.clear()
        self.assertFalse(restart_signal.is_restart_requested())

    def test_request_is_idempotent(self):
        restart_signal.request_restart()
        restart_signal.request_restart()
        self.assertTrue(restart_signal.is_restart_requested())


class TestSaveRequestWantsRestart(unittest.TestCase):
    """The dispatcher in websocket_flow parses the raw request body
    via this helper to decide whether to schedule a shutdown."""

    def _check(self, body: str) -> bool:
        from app.io.websocket_flow import _save_request_wants_restart
        return _save_request_wants_restart(body)

    def test_true_when_restart_flag_set(self):
        self.assertTrue(self._check('{"updates": {}, "restart": true}'))

    def test_false_when_restart_flag_unset(self):
        self.assertFalse(self._check('{"updates": {}}'))

    def test_false_when_restart_explicitly_false(self):
        self.assertFalse(self._check('{"updates": {}, "restart": false}'))

    def test_false_on_malformed_json(self):
        """Bad JSON shouldn't trigger an accidental shutdown."""
        self.assertFalse(self._check("{not json"))
        self.assertFalse(self._check(""))

    def test_falsy_restart_values_treated_as_false(self):
        self.assertFalse(self._check('{"restart": 0}'))
        self.assertFalse(self._check('{"restart": ""}'))
        self.assertFalse(self._check('{"restart": null}'))


class TestPerformRestart(unittest.TestCase):
    """``perform_restart`` exits with the sentinel code the ``swarpius``
    supervisor recognises as "respawn me". The supervisor (its own
    process) is what actually handles the relaunch — see
    ``test_supervisor.py`` for the supervisor side of the contract."""

    def test_perform_restart_exits_with_sentinel_code(self):
        with self.assertRaises(SystemExit) as ctx:
            restart_signal.perform_restart()
        self.assertEqual(ctx.exception.code, restart_signal.RESTART_EXIT_CODE)

    def test_sentinel_matches_supervisor_constant(self):
        """If these drift apart the supervisor stops recognising the
        agent's restart request — silent regression. Pin them together."""
        from swarpius import RESTART_EXIT_CODE as SUPERVISOR_CODE
        self.assertEqual(restart_signal.RESTART_EXIT_CODE, SUPERVISOR_CODE)


if __name__ == "__main__":
    unittest.main()
