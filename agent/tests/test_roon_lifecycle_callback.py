"""Roon connection lifecycle callback for CLI startup feedback.

When the CLI starts ``runtime.ensure_initialised()`` runs Roon
discovery, then either pairs (first run, blocks ~120s waiting
for the user to approve in Roon Settings) or connects (returning
user). Without lifecycle visibility the CLI sits silent for that
window and looks hung — especially the pairing case where the
user needs to actively do something in another app.

``RoonAuthMixin`` and ``RoonConnection`` accept an optional
``lifecycle_callback(message: str)`` and call it at each stage:

  * "Discovering Roon Cores on the network…" (from
    ``_discover_and_pair``)
  * "Pairing — please approve the Swarpius extension in Roon
    Settings → Extensions" (from ``_perform_auth``)
  * "Authorised on <core_name>" (from ``_perform_auth`` after
    the user approves)

This module's tests exercise the auth methods in isolation with
mocked RoonApi / RoonDiscovery so the callback wiring is pinned
without needing a live Roon Core.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_host(callback=None, tmpdir=None):
    """Mixin-using host. Subclasses RoonAuthMixin so all its
    methods are bound the same way they would be on a real
    RoonConnection.

    The auth file paths are instance attributes on RoonConnection
    in production; we mirror that here by setting them on the host
    so ``_write_id_and_token`` writes to a tempdir instead of the
    real config dir.
    """
    from roon_core.auth import RoonAuthMixin

    base = Path(tmpdir) if tmpdir else Path(tempfile.mkdtemp(prefix="roon-auth-test-"))

    class _Host(RoonAuthMixin):
        def __init__(self):
            self._lifecycle_cb = callback or (lambda _msg: None)
            self._core_id_path = base / "roon_core_id"
            self._token_path = base / "roon_core_token"

        def _notify_lifecycle(self, message: str) -> None:
            self._lifecycle_cb(message)

    return _Host()


class TestPerformAuthCallback(unittest.TestCase):
    def test_pairing_message_fires_before_wait(self) -> None:
        """Operator needs to know they have to approve in Roon
        BEFORE the wait loop starts blocking."""
        messages: list[str] = []
        host = _make_host(callback=messages.append)

        # Mock RoonApi so the wait loop terminates immediately.
        fake_api = MagicMock()
        fake_api.token = "dummy-token"
        fake_api.core_id = "dummy-core-id"
        fake_api.core_name = "Test Core"
        fake_api.host = "192.168.1.100"

        with patch("roon_core.auth.RoonApi", return_value=fake_api):
            host._perform_auth({"display_name": "Swarpius"}, ("192.168.1.100", 9100))

        self.assertTrue(messages, "callback was never fired")
        # First message must be the pairing prompt — operator action
        # required before anything else can happen.
        self.assertIn("approve", messages[0].lower())
        # Subsequent message confirms the pairing landed.
        self.assertTrue(
            any("authoris" in m.lower() or "connect" in m.lower() for m in messages),
            f"expected post-pairing confirmation in {messages!r}",
        )


class TestDiscoverAndPairCallback(unittest.TestCase):
    def test_discovering_message_fires(self) -> None:
        messages: list[str] = []
        host = _make_host(callback=messages.append)

        fake_core = MagicMock()
        fake_core.host = "192.168.1.100"
        fake_core.port = 9100
        fake_core.core_name = "Test Core"
        fake_core.core_id = "dummy-id"

        fake_api = MagicMock()
        fake_api.token = "dummy-token"
        fake_api.core_id = "dummy-id"
        fake_api.core_name = "Test Core"
        fake_api.host = "192.168.1.100"

        with (
            patch("roon_core.auth.discover_cores", return_value=[fake_core]),
            patch("roon_core.auth.select_core", return_value=fake_core),
            patch("roon_core.auth.RoonApi", return_value=fake_api),
            patch("app.settings.get_settings") as gs,
        ):
            gs.return_value.roon_core_name = None
            host._discover_and_pair({"display_name": "Swarpius"})

        self.assertTrue(messages, "callback was never fired")
        self.assertIn("discover", messages[0].lower())


class TestNoCallbackIsSafe(unittest.TestCase):
    def test_default_does_nothing(self) -> None:
        host = _make_host()  # no callback
        # Must not raise.
        host._notify_lifecycle("anything")


if __name__ == "__main__":
    unittest.main()
