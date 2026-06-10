"""Tests for default zone payload generation and broadcast on zone changes."""

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.constants import CHANNEL_DEFAULT_ZONE_UPDATE  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402
from roon_core.zones import RoonZoneMixin  # noqa: E402


class _FakeApi:
    def __init__(self):
        self.zones = {
            "zone_lr": {"zone_id": "zone_lr", "display_name": "Living Room",
                        "outputs": [{"output_id": "out_lr", "display_name": "Living Room"}]},
            "zone_k": {"zone_id": "zone_k", "display_name": "Kitchen",
                       "outputs": [{"output_id": "out_k", "display_name": "Kitchen"}]},
            "zone_o": {"zone_id": "zone_o", "display_name": "Office",
                       "outputs": [{"output_id": "out_o", "display_name": "Office"}]},
        }

    def transfer_zone(self, from_output_id, to_output_id):
        """Stub of the Roon API's transfer call — the network
        boundary that production hands off to."""
        _ = from_output_id, to_output_id


class _FakeRoonConnection(RoonZoneMixin):
    """Real ``RoonZoneMixin`` over a stubbed Roon API.

    All zone-resolution logic — ``target_zone`` property,
    ``get_default_zone()`` (including ``_resolve_default_zone``
    fall-back), ``_find_zone_by_name`` etc. — runs on the call path
    so tests exercise production code, not the fake. Only the
    network boundary (``api.zones``) and the event-listener
    registration are stubbed.
    """

    def __init__(self, default_zone=None, **kwargs):
        _ = kwargs
        self.api = _FakeApi()
        self._default_zone_name = default_zone
        self._preferred_output_id = None
        self._preferred_zone_label = None
        # Mirrors what the real connection does after Roon pairs:
        # seed the preferred output handle from the configured name
        # so subsequent online/offline transitions are observable.
        self._resolve_default_zone()

    def register_event_listener(self, listener):
        _ = listener


@contextmanager
def _init_runtime(extra_env=None):
    runtime = RuntimeState()
    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
    }
    if extra_env:
        env.update(extra_env)

    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", _FakeRoonConnection),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch("app.runtime.state._format_agent_skills_for_prompt", return_value=("", "")),
        tempfile.TemporaryDirectory() as tmp_dir,
    ):
        runtime.zone_aliases_path = Path(tmp_dir) / "zone_aliases.json"
        runtime.ensure_initialised()
        yield runtime


class TestGetDefaultZonePayload(unittest.TestCase):
    """Tests for RuntimeState.get_default_zone_payload()."""

    def test_returns_zone_name_when_set(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {}
            payload = runtime.get_default_zone_payload()
            self.assertEqual(payload["zone_name"], "Living Room")
            self.assertIsNone(payload["alias"])

    def test_returns_alias_when_present(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Lounge": "out_lr"}
            payload = runtime.get_default_zone_payload()
            self.assertEqual(payload["zone_name"], "Living Room")
            self.assertEqual(payload["alias"], "Lounge")

    def test_returns_none_zone_when_no_preference_and_no_zones(self):
        """Payload is empty when nothing has been preferred yet AND
        Roon hasn't reported any zones — the resolve-on-demand path
        has nothing to adopt."""
        with _init_runtime() as runtime:
            runtime.roon_connection._preferred_output_id = None
            runtime.roon_connection._default_zone_name = None
            runtime.roon_connection._preferred_zone_label = None
            runtime.roon_connection.api.zones.clear()
            payload = runtime.get_default_zone_payload()
            self.assertIsNone(payload["zone_name"])
            self.assertIsNone(payload["alias"])

    def test_is_online_true_when_zone_in_api_zones(self):
        with _init_runtime() as runtime:
            payload = runtime.get_default_zone_payload()
            self.assertTrue(payload["is_online"])

    def test_is_online_false_when_zone_not_in_api_zones(self):
        """The chosen zone disappearing from Roon mid-session (e.g.
        BT headphones going to standby) must not silently re-route
        the default to some other online zone — the frontend
        renders the offline state and the user keeps their choice."""
        with _init_runtime() as runtime:
            runtime.roon_connection.api.zones.pop("zone_lr")
            payload = runtime.get_default_zone_payload()
            self.assertEqual(payload["zone_name"], "Living Room")
            self.assertFalse(payload["is_online"])

    def test_offline_zone_returns_online_when_it_comes_back(self):
        """After the chosen zone goes offline and then reappears,
        the default flips back to it automatically (preferred output
        was preserved across the offline window)."""
        with _init_runtime() as runtime:
            saved_lr = runtime.roon_connection.api.zones.pop("zone_lr")
            offline_payload = runtime.get_default_zone_payload()
            self.assertFalse(offline_payload["is_online"])

            runtime.roon_connection.api.zones["zone_lr"] = saved_lr
            back_payload = runtime.get_default_zone_payload()
            self.assertEqual(back_payload["zone_name"], "Living Room")
            self.assertTrue(back_payload["is_online"])

    def test_offline_zone_stays_chosen_across_repeated_payload_reads(self):
        """Repeatedly reading the payload while the chosen zone is
        offline must not flip the preference — every call returns
        the same offline zone."""
        with _init_runtime() as runtime:
            runtime.roon_connection.api.zones.pop("zone_lr")
            for _ in range(3):
                payload = runtime.get_default_zone_payload()
                self.assertEqual(payload["zone_name"], "Living Room")
                self.assertFalse(payload["is_online"])

    def test_is_online_false_when_no_zone_resolved(self):
        with _init_runtime() as runtime:
            runtime.roon_connection._preferred_output_id = None
            runtime.roon_connection._default_zone_name = None
            runtime.roon_connection._preferred_zone_label = None
            runtime.roon_connection.api.zones.clear()
            payload = runtime.get_default_zone_payload()
            self.assertFalse(payload["is_online"])


class TestDefaultZoneBroadcast(unittest.TestCase):
    """Tests that perform_config_action broadcasts default zone updates."""

    def test_set_default_zone_broadcasts(self):
        with _init_runtime() as runtime:
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send

            runtime.perform_config_action("Set Default Zone", zone="Kitchen")

            ws_send.assert_any_call(
                CHANNEL_DEFAULT_ZONE_UPDATE,
                {"zone_name": "Kitchen", "alias": None, "group_name": None,
                 "is_grouped": False, "is_online": True},
            )

    def test_set_default_zone_broadcasts_with_alias(self):
        with _init_runtime() as runtime:
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send
            runtime.zone_aliases = {"Cook": "out_k"}

            runtime.perform_config_action("Set Default Zone", zone="Kitchen")

            ws_send.assert_any_call(
                CHANNEL_DEFAULT_ZONE_UPDATE,
                {"zone_name": "Kitchen", "alias": "Cook", "group_name": None,
                 "is_grouped": False, "is_online": True},
            )

    def test_transfer_zone_does_not_change_default_or_broadcast(self):
        """Transfer Zone moves playback but must leave the default
        zone alone — the user's choice survives a routine transfer."""
        with _init_runtime() as runtime:
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send
            self.assertEqual(runtime.roon_connection.target_zone, "Living Room")

            runtime.perform_config_action(
                "Transfer Zone",
                zone="Living Room",
                zone_to_transfer_to="Office",
            )

            self.assertEqual(runtime.roon_connection.target_zone, "Living Room")
            default_zone_calls = [
                c for c in ws_send.call_args_list
                if c.args and c.args[0] == CHANNEL_DEFAULT_ZONE_UPDATE
            ]
            self.assertEqual(default_zone_calls, [])

    def test_get_default_zone_does_not_broadcast(self):
        with _init_runtime() as runtime:
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send

            runtime.perform_config_action("Get Default Zone")

            ws_send.assert_not_called()

    def test_set_zone_alias_broadcasts_if_aliased_zone_is_default(self):
        """Setting an alias on the current default zone broadcasts a
        default-zone update so the frontend picks up the new alias.
        (A zone-artwork update also fires for all alias changes; we
        only assert the default-zone-specific one here.)"""
        with _init_runtime() as runtime:
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send

            runtime.perform_config_action(
                "Set Zone Alias", zone="Living Room", alias="Lounge",
            )

            ws_send.assert_any_call(
                CHANNEL_DEFAULT_ZONE_UPDATE,
                {"zone_name": "Living Room", "alias": "Lounge", "group_name": None,
                 "is_grouped": False, "is_online": True},
            )

    def test_set_zone_alias_does_not_broadcast_default_zone_for_non_default(self):
        """Aliasing a non-default zone broadcasts zone-artwork but
        not a default-zone update — the default itself didn't change."""
        with _init_runtime() as runtime:
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send

            runtime.perform_config_action(
                "Set Zone Alias", zone="Kitchen", alias="Cook",
            )

            default_zone_calls = [
                c for c in ws_send.call_args_list
                if c.args and c.args[0] == CHANNEL_DEFAULT_ZONE_UPDATE
            ]
            self.assertEqual(default_zone_calls, [])

    def test_remove_zone_alias_broadcasts_if_aliased_zone_is_default(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Lounge": "out_lr"}
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send

            runtime.perform_config_action("Remove Zone Alias", alias="Lounge")

            ws_send.assert_any_call(
                CHANNEL_DEFAULT_ZONE_UPDATE,
                {"zone_name": "Living Room", "alias": None, "group_name": None,
                 "is_grouped": False, "is_online": True},
            )

    def test_remove_zone_alias_does_not_broadcast_default_zone_for_non_default(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Cook": "out_k"}
            ws_send = MagicMock()
            runtime._ws_send_callback = ws_send

            runtime.perform_config_action("Remove Zone Alias", alias="Cook")

            default_zone_calls = [
                c for c in ws_send.call_args_list
                if c.args and c.args[0] == CHANNEL_DEFAULT_ZONE_UPDATE
            ]
            self.assertEqual(default_zone_calls, [])


class TestZoneAliasesFileIsolation(unittest.TestCase):
    """Verify tests never write to the real zone_aliases.json.

    Belt-and-braces sentinel: even with the conftest-level ``SWARPIUS_DATA_DIR``
    redirect, this test pins the contract that the user's real config file
    is untouched after a save through RuntimeState.
    """

    def test_save_zone_aliases_writes_to_temp_path_not_real_file(self):
        from app.data_paths import AGENT_ROOT

        # Real config path post-data-reorg. Tests must never touch this.
        real_path = AGENT_ROOT / "data" / "config" / "zone_aliases.json"
        baseline = real_path.read_text() if real_path.exists() else None

        with _init_runtime() as runtime:
            runtime.zone_aliases = {"TestAlias": "TestZone"}
            runtime._save_zone_aliases()

            # Written to temp path under the redirect.
            self.assertTrue(runtime.zone_aliases_path.exists())
            self.assertIn("TestAlias", runtime.zone_aliases_path.read_text())

            # The conftest redirect points config_dir at a session temp,
            # so the temp path must NOT equal the real path.
            self.assertNotEqual(runtime.zone_aliases_path, real_path)

        # Real file untouched — content unchanged (or still absent).
        after = real_path.read_text() if real_path.exists() else None
        self.assertEqual(
            after, baseline,
            f"Test leaked into the real zone_aliases.json at {real_path}",
        )


if __name__ == "__main__":
    unittest.main()
