"""A startup failure surfaces a clean one-line message and exits(1) rather
than dumping a traceback. Shared by the CLI init path and main()'s startup
guard, so a transient Roon discovery failure reads cleanly in both modes."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


class TestReportStartupFailure(unittest.TestCase):
    def test_prints_clean_message_and_exits_without_traceback(self):
        import agent

        console = MagicMock()
        with (
            patch.object(agent, "get_console", return_value=console),
            patch.object(agent, "_log_file", None),
        ):
            with self.assertRaises(SystemExit) as ctx:
                agent._report_startup_failure(
                    ConnectionError("Discovery failed to find any Roon Cores"),
                )

        self.assertEqual(ctx.exception.code, 1)
        printed = " ".join(str(c.args) for c in console.print.call_args_list)
        self.assertIn("Startup failed", printed)
        self.assertIn("Roon Cores", printed)
