"""Behavioural tests for RoonConnection.__init__.

Stubs only at the external boundaries — the third-party ``RoonApi`` and
``RoonDiscovery`` classes, and the on-disk token files. The real
``_get_id_and_token``, ``_lookup_known_core``, ``_perform_auth``, and
``_discover_and_pair`` mixin methods all run end-to-end. Tests assert
on observable outcomes (which Roon Core address ``RoonApi`` was
constructed against, target_zone resolution, log lines) rather than on
internal call sequence.
"""

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import List
from unittest.mock import ANY, MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.connection import APP_INFO, RoonConnection  # noqa: E402


def _make_api(zones=None):
    """MagicMock RoonApi instance with the minimal surface RoonConnection
    accesses during init (zones, register_*_callback for live subs)."""
    api = MagicMock()
    api.zones = zones if zones is not None else {
        "zone-1": {
            "display_name": "Living Room", "zone_id": "zone-1",
            "outputs": [{"output_id": "o-1", "display_name": "Living Room"}],
        },
    }
    # Default core_id/token set on the instance for pairing tests where
    # _perform_auth's polling loop reads them off RoonApi.
    api.core_id = "fake-core-id"
    api.core_name = "Fake Core"
    api.host = "fake-host"
    api.token = "fake-token"
    return api


@contextmanager
def _stub_environment(
    saved_auth: dict | None = None,
    api_factory=None,
    discovery_host_port: tuple[str, int] | None = ("10.0.0.20", 9330),
    discovered_cores: List | None = None,
):
    """Set up a clean RoonConnection init environment.

    - Token files redirected to a tempdir. If ``saved_auth`` is provided,
      the files start populated with ``core_id`` and ``token``;
      otherwise they don't exist (first-run / fresh state).
    - ``RoonApi`` constructor returns a MagicMock api per ``api_factory``
      (default: stub api with one zone). Init-call args are observable
      via the patched class's call_args_list.
    - ``RoonDiscovery`` (used by ``_lookup_known_core``) yields
      ``discovery_host_port``; ``discover_cores`` (used by
      ``_discover_and_pair``) yields ``discovered_cores``.
    """
    if api_factory is None:
        api_factory = _make_api

    with tempfile.TemporaryDirectory() as tmp:
        tmp_core_id = Path(tmp) / "roon_core_id"
        tmp_token = Path(tmp) / "roon_core_token"
        if saved_auth is not None:
            tmp_core_id.write_text(saved_auth["core_id"], encoding="utf-8")
            tmp_token.write_text(saved_auth["token"], encoding="utf-8")

        # Build a fresh api each time RoonApi is invoked so pairing tests
        # (which construct RoonApi twice — once for auth, once for the
        # real connection) get distinct instances. Capture init args
        # across both connection-side and auth-side patches.
        api_instances: list = []
        api_init_calls: list = []

        def make_api(*args, **kwargs):
            api_init_calls.append(args)
            inst = api_factory()
            api_instances.append(inst)
            return inst

        discovery = MagicMock()
        discovery.first.return_value = discovery_host_port
        # When _discover_and_pair runs, it calls discover_cores() and
        # select_core(). Provide enough to mint a chosen DiscoveredCore.
        from roon_core.discovery import DiscoveredCore
        cores = discovered_cores if discovered_cores is not None else [
            DiscoveredCore(
                host="10.0.0.30", port=9330,
                core_id="discovered-core-id", core_name="Discovered Core",
            ),
        ]

        with (
            patch("roon_core.connection.RoonApi", side_effect=make_api) as roon_api_cls,
            patch("roon_core.auth.RoonApi", side_effect=make_api),
            patch("roon_core.auth.RoonDiscovery", return_value=discovery),
            patch("roon_core.auth.discover_cores", return_value=cores),
        ):
            yield {
                "roon_api_cls": roon_api_cls,
                "api_instances": api_instances,
                "api_init_calls": api_init_calls,
                "discovery": discovery,
                "tmp_core_id": tmp_core_id,
                "tmp_token": tmp_token,
                # Spread-friendly kwargs for RoonConnection so each test
                # can pass ``**env["auth_paths"]`` rather than threading
                # individual arg names through every call site.
                "auth_paths": {
                    "core_id_path": tmp_core_id,
                    "token_path": tmp_token,
                },
            }


class TestConfiguredServer(unittest.TestCase):
    """When ROON_CORE_URL is set, RoonConnection pairs/connects directly
    to that address — no SOOD discovery."""

    def test_uses_configured_server_when_host_and_port_are_provided(self):
        """Saved token + configured URL → connect directly without
        discovery."""
        with _stub_environment(
            saved_auth={"core_id": "core-a", "token": "token-a"},
        ) as env:
            RoonConnection(
                default_zone="Living Room",
                roon_core_host="10.0.0.10",
                roon_core_port=9330,
                **env["auth_paths"],
            )

        # RoonApi constructed exactly once, against the configured server,
        # with the saved token.
        self.assertEqual(env["roon_api_cls"].call_count, 1)
        args = env["roon_api_cls"].call_args.args
        self.assertEqual(args, (APP_INFO, "token-a", "10.0.0.10", 9330, True))
        # Discovery path (RoonDiscovery.first) NOT invoked.
        env["discovery"].first.assert_not_called()

    def test_configured_url_used_for_pairing_when_token_missing(self):
        """ROON_CORE_URL set + no saved token (fresh Docker setup): pair
        with the configured URL directly, not via SOOD discovery."""
        def api_factory():
            api = _make_api()
            api.token = "token-z"  # post-auth token surfaced by RoonApi
            api.core_id = "core-z"
            return api

        with _stub_environment(
            saved_auth=None,
            api_factory=api_factory,
        ) as env:
            RoonConnection(
                default_zone="Living Room",
                roon_core_host="10.0.0.10",
                roon_core_port=9330,
                **env["auth_paths"],
            )

        # Two RoonApi constructions: once for _perform_auth (token=None,
        # blocking=False), once for the real connection (token=saved,
        # blocking=True). Both target the configured server. Discovery
        # never runs.
        self.assertEqual(len(env["api_init_calls"]), 2)
        self.assertEqual(
            env["api_init_calls"][0],
            (APP_INFO, None, "10.0.0.10", 9330, False),
        )
        self.assertEqual(
            env["api_init_calls"][1],
            (APP_INFO, "token-z", "10.0.0.10", 9330, True),
        )
        env["discovery"].first.assert_not_called()


class TestDiscoveryPath(unittest.TestCase):
    """When no ROON_CORE_URL is set, RoonConnection discovers a Core via
    SOOD (first-run) or by core_id lookup (subsequent runs)."""

    def test_loads_saved_auth_and_discovers_server_by_core_id(self):
        """Saved token + no configured URL → look up Core by core_id."""
        with _stub_environment(
            saved_auth={"core_id": "core-b", "token": "token-b"},
            discovery_host_port=("10.0.0.20", 9330),
        ) as env:
            RoonConnection(default_zone="Living Room", **env["auth_paths"])

        # RoonDiscovery locates the saved core_id.
        env["discovery"].first.assert_called_once()
        # RoonApi connected to the discovered address with saved token.
        self.assertEqual(env["roon_api_cls"].call_count, 1)
        args = env["roon_api_cls"].call_args.args
        self.assertEqual(args, (APP_INFO, "token-b", "10.0.0.20", 9330, True))

    def test_discovery_returning_none_address_raises_clear_error(self):
        """A core_id lookup that yields a (None, None) address — a transient
        discovery hiccup — must raise a clear ConnectionError, not crash on
        int(None) when building the port."""
        with _stub_environment(
            saved_auth={"core_id": "core-b", "token": "token-b"},
            discovery_host_port=(None, None),
        ) as env:
            with self.assertRaises(ConnectionError) as ctx:
                RoonConnection(default_zone="Living Room", **env["auth_paths"])
            self.assertIn("Roon Core", str(ctx.exception))
            # Never tried to construct RoonApi against the bogus address.
            env["roon_api_cls"].assert_not_called()

    def test_performs_auth_when_saved_auth_missing(self):
        """No saved token → discover and pair, save the token, then
        initialise the API."""
        def api_factory():
            api = _make_api()
            api.token = "token-c"  # post-auth token
            api.core_id = "core-c"
            return api

        with _stub_environment(
            saved_auth=None,
            api_factory=api_factory,
        ) as env:
            RoonConnection(default_zone="Living Room", **env["auth_paths"])

            # _perform_auth constructs once (token=None, blocking=False),
            # then real connection constructs again (token=saved from file).
            self.assertEqual(len(env["api_init_calls"]), 2)
            # First call targets the SOOD-discovered host:port.
            self.assertEqual(
                env["api_init_calls"][0],
                (APP_INFO, None, "10.0.0.30", 9330, False),
            )
            # Second uses the token persisted by _perform_auth.
            self.assertEqual(
                env["api_init_calls"][1],
                (APP_INFO, "token-c", "10.0.0.30", 9330, True),
            )
            # Token persisted to disk.
            self.assertEqual(env["tmp_core_id"].read_text(encoding="utf-8"), "core-c")
            self.assertEqual(env["tmp_token"].read_text(encoding="utf-8"), "token-c")

    def test_raises_when_token_still_missing_after_pairing(self):
        """If pairing returns a server but the token file can't be read
        afterwards, surface an OSError. We simulate this by making
        _perform_auth a stub (token files never written) — token-read
        on the second attempt will then fail."""
        def api_factory():
            api = _make_api()
            # Set token so _perform_auth's wait loop exits cleanly, but
            # don't trigger the file write because we patch the write
            # paths below to do nothing.
            api.token = "irrelevant"
            api.core_id = "irrelevant"
            return api

        with _stub_environment(saved_auth=None, api_factory=api_factory) as env:
            # Suppress the file writes that _perform_auth normally does
            # so the second _get_id_and_token raises OSError.
            with patch("roon_core.auth.open", side_effect=OSError("read-only")):
                with self.assertRaises(OSError):
                    RoonConnection(default_zone="Living Room", **env["auth_paths"])


class TestProfileApplication(unittest.TestCase):
    """When profile=X is provided, RoonConnection walks the Roon Settings
    menu to confirm the profile is currently selected."""

    def test_applies_profile_when_profile_name_is_provided(self):
        """Successful profile walk logs the success line. Production
        confirms by checking that the terminal menu item has
        subtitle='selected'."""
        path = ["Settings", "Profile", "Custom Profile", "Profile", "Custom Profile"]
        find_results = [
            MagicMock(item_key=f"k-{seg}", subtitle="")
            for seg in path[:-1]
        ]
        find_results.append(
            MagicMock(item_key="k-final", subtitle="selected"),
        )

        with (
            _stub_environment(
                saved_auth={"core_id": "core-profile", "token": "token-profile"},
            ) as env,
            patch(
                "roon_core.connection.RoonCoreResultsSchema",
                return_value=MagicMock(items=[]),
            ),
            patch(
                "roon_core.connection.RoonConnection.find_item_by_field",
                side_effect=find_results,
            ),
            self.assertLogs("roon_core.connection", level="INFO") as cm,
        ):
            RoonConnection(
                default_zone="Living Room",
                profile="Custom Profile",
                **env["auth_paths"],
            )

        self.assertTrue(
            any("Successfully set profile 'Custom Profile'" in m for m in cm.output),
            f"Expected success log; got {cm.output}",
        )

    def test_logs_warning_when_profile_cannot_be_applied(self):
        """browse_load failure during profile walk → caught and logged
        as a warning; init still succeeds with the default profile."""
        def api_factory():
            api = _make_api()
            api.browse_load.side_effect = RuntimeError("browse_load failed")
            return api

        with (
            _stub_environment(
                saved_auth={"core_id": "core-profile", "token": "token-profile"},
                api_factory=api_factory,
            ) as env,
            patch(
                "roon_core.connection.RoonCoreResultsSchema",
                return_value=MagicMock(items=[]),
            ),
            patch("roon_core.connection.logger.warning") as warning_mock,
        ):
            RoonConnection(
                default_zone="Living Room",
                profile="Broken Profile",
                **env["auth_paths"],
            )

        warning_mock.assert_called_once_with(
            "Unable to apply Roon profile '%s'; continuing with default profile. Cause: %s",
            "Broken Profile",
            ANY,
        )


class TestDefaultZoneResolution(unittest.TestCase):
    """Observable target_zone behaviour at init time and lazily."""

    def test_default_zone_falls_back_to_first_reported_when_unset(self):
        """No DEFAULT_ROON_ZONE → adopt the first reported zone so users
        can run Swarpius without knowing zone names up-front."""
        api = _make_api(zones={
            "zone-first": {
                "display_name": "Kitchen", "zone_id": "zone-first",
                "outputs": [{"output_id": "o-k", "display_name": "Kitchen"}],
            },
            "zone-second": {
                "display_name": "Lounge", "zone_id": "zone-second",
                "outputs": [{"output_id": "o-l", "display_name": "Lounge"}],
            },
        })
        with _stub_environment(
            saved_auth={"core_id": "core-x", "token": "token-x"},
            api_factory=lambda: api,
        ) as env:
            conn = RoonConnection(default_zone=None, **env["auth_paths"])

        self.assertEqual(conn.target_zone, "Kitchen")
        self.assertEqual(conn.get_default_zone(), "Kitchen")

    def test_default_zone_lazy_resolves_when_zones_appear_later(self):
        """If zones aren't reported at init time, get_default_zone should
        resolve once they show up — covers the 'Roon Core still loading'
        path in _resolve_default_zone_group_fallback."""
        api = _make_api(zones={})
        with _stub_environment(
            saved_auth={"core_id": "core-y", "token": "token-y"},
            api_factory=lambda: api,
        ) as env:
            conn = RoonConnection(default_zone="", **env["auth_paths"])

        self.assertIsNone(conn.target_zone)
        # Zones appear later — the same api dict is mutated.
        api.zones["zone-a"] = {
            "display_name": "Bedroom", "zone_id": "zone-a",
            "outputs": [{"output_id": "o-bed", "display_name": "Bedroom"}],
        }
        self.assertEqual(conn.get_default_zone(), "Bedroom")
        self.assertEqual(conn.target_zone, "Bedroom")


if __name__ == "__main__":
    unittest.main()
