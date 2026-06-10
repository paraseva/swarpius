"""Tests for clean error handling when parallel_browse times out.

When a Roon browse request times out (or the socket disconnects), the
patched ``parallel_browse`` returns ``None`` rather than raising. The
``_browse_core_once`` path must turn that into a clear
``ExternalServiceError`` rather than letting Pydantic fail validation
on ``model_validate(None)`` with a misleading "1 validation error"
message.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.exceptions import ExternalServiceError  # noqa: E402
from roon_core.browse import RoonBrowseMixin  # noqa: E402
from roon_core.browse_session import BrowseSessionManager  # noqa: E402


def _make_conn(api):
    class MinimalConn(RoonBrowseMixin):
        def __init__(self):
            self.session_manager = BrowseSessionManager()
            self.api = api

        def _build_browse_opts(self, zone, session_key):
            return {}

    return MinimalConn()


class TestBrowseCoreTimeoutSurfacing(unittest.TestCase):
    def test_browse_browse_returning_none_raises_clean_error(self):
        api = MagicMock()
        api.browse_browse.return_value = None  # simulate timeout / disconnect
        api.browse_load = MagicMock()  # should never be called
        conn = _make_conn(api)

        with self.assertRaises(ExternalServiceError) as ctx:
            conn._browse_core_once({"input": "test"}, session_key="s-1")

        self.assertIn("browse_browse returned no response", str(ctx.exception))
        self.assertIn("timed out or socket disconnected", str(ctx.exception))
        api.browse_load.assert_not_called()

    def test_browse_load_returning_none_raises_clean_error(self):
        api = MagicMock()
        api.browse_browse.return_value = {"action": "list"}
        api.browse_load.return_value = None  # simulate timeout / disconnect
        conn = _make_conn(api)

        with self.assertRaises(ExternalServiceError) as ctx:
            conn._browse_core_once({"input": "test"}, session_key="s-1")

        msg = str(ctx.exception)
        self.assertIn("browse_load returned no response", msg)
        self.assertIn("timed out or socket disconnected", msg)
        # The original misleading Pydantic error must not leak through.
        self.assertNotIn("validation error", msg.lower())
        self.assertNotIn("RoonCoreResultsSchema", msg)


if __name__ == "__main__":
    unittest.main()
