"""Tests for unified zone name resolution and zone alias context provider.

Fake connection inherits ``RoonZoneMixin`` so the real zone-resolution
methods (``_find_zone_by_name``, ``get_zone_display_name``,
``get_zone_names``, ``is_zone_grouped``, ``set_default_zone``,
``get_default_zone``, ``transfer_zone``) execute on the call path.
Only the Roon-API surface (``api.zones`` data plus tracked transport-call
side effects) is stubbed.
"""

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.exceptions import ZoneLookupError  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402
from roon_core.zones import RoonZoneMixin  # noqa: E402


def _default_zones() -> dict:
    return {
        "zone_lr": {
            "zone_id": "zone_lr", "display_name": "Living Room",
            "outputs": [{"output_id": "out_lr", "display_name": "Living Room"}],
        },
        "zone_k": {
            "zone_id": "zone_k", "display_name": "Kitchen",
            "outputs": [{"output_id": "out_k", "display_name": "Kitchen"}],
        },
        "zone_o": {
            "zone_id": "zone_o", "display_name": "Office",
            "outputs": [{"output_id": "out_o", "display_name": "Office"}],
        },
    }


class _FakeRoonConnection(RoonZoneMixin):
    """Boundary stub: real RoonZoneMixin methods run; only the api/data
    layer and side-effecting transport methods are stubbed.

    Captures playback_control / get_zone_snapshot / transfer_zone calls
    so tool-level tests can assert on the connection-boundary call shape
    (the contract the tool guarantees crossing the boundary).
    """

    def __init__(self, default_zone=None, **_kwargs):
        # Accept RoonConnection's full kwarg surface (default_zone,
        # roon_core_host, roon_core_port, profile, lifecycle_callback,
        # zones override) so RuntimeState can construct us in place.
        zones = _kwargs.get("zones")
        default_output = _kwargs.get("default_output", "out_lr")
        self.api = SimpleNamespace(
            zones=zones if zones is not None else _default_zones(),
        )
        self._default_zone_name: str | None = default_zone
        self._preferred_output_id = default_output
        self.playback_calls: list[dict] = []
        self.zone_snapshot_calls: list[dict] = []
        self.zones_snapshot_calls = 0
        self.transfer_calls: list[tuple[str, str]] = []
        self.event_listeners: list = []

    # Transport / API surface stubs (the real boundary)
    def register_event_listener(self, listener):
        self.event_listeners.append(listener)

    def playback_control(self, **kwargs):
        self.playback_calls.append(kwargs)

    def get_zone_snapshot(self, zone=None):
        self.zone_snapshot_calls.append({"zone": zone})
        return {
            "display_name": zone or "Living Room",
            "state": "playing",
            "settings": {},
            "now_playing": {"three_line": {}},
        }

    def get_zones_snapshot(self):
        self.zones_snapshot_calls += 1
        return [
            {
                "display_name": z["display_name"],
                "state": "stopped",
                "settings": {},
                "now_playing": {"three_line": {}},
                "outputs": z["outputs"],
            }
            for z in self.api.zones.values()
        ]

    def transfer_zone(self, from_zone, to_zone):
        self.transfer_calls.append((from_zone, to_zone))


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


# ── resolve_zone_name tests ─────────────────────────────────────────────────


class TestResolveZoneName(unittest.TestCase):
    """Tests for RuntimeState.resolve_zone_name — single resolution point."""

    def test_exact_zone_name_returns_display_name(self):
        with _init_runtime() as runtime:
            self.assertEqual(runtime.resolve_zone_name("Kitchen"), "Kitchen")

    def test_case_insensitive_zone_match(self):
        with _init_runtime() as runtime:
            self.assertEqual(runtime.resolve_zone_name("kitchen"), "Kitchen")
            self.assertEqual(runtime.resolve_zone_name("LIVING ROOM"), "Living Room")

    def test_alias_resolves_to_real_zone(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k"}
            self.assertEqual(runtime.resolve_zone_name("Potato"), "Kitchen")

    def test_alias_case_insensitive(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k"}
            self.assertEqual(runtime.resolve_zone_name("potato"), "Kitchen")
            self.assertEqual(runtime.resolve_zone_name("POTATO"), "Kitchen")

    def test_real_zone_takes_precedence_over_alias(self):
        """If an alias name collides with a real zone, real zone wins."""
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Kitchen": "out_o"}
            self.assertEqual(runtime.resolve_zone_name("Kitchen"), "Kitchen")

    def test_unknown_zone_raises_zone_lookup_error(self):
        with _init_runtime() as runtime:
            with self.assertRaises(ZoneLookupError) as ctx:
                runtime.resolve_zone_name("Nonexistent Zone")
            self.assertIn("Nonexistent Zone", str(ctx.exception))
            self.assertIn("alias", str(ctx.exception).lower())

    def test_empty_string_raises_zone_lookup_error(self):
        with _init_runtime() as runtime:
            with self.assertRaises(ZoneLookupError):
                runtime.resolve_zone_name("")

    def test_whitespace_only_raises_zone_lookup_error(self):
        with _init_runtime() as runtime:
            with self.assertRaises(ZoneLookupError):
                runtime.resolve_zone_name("   ")

    def test_fuzzy_partial_match(self):
        with _init_runtime() as runtime:
            # "Living" uniquely matches "Living Room"
            self.assertEqual(runtime.resolve_zone_name("Living"), "Living Room")

    def test_alias_with_stale_zone_is_ignored(self):
        """An alias pointing to a zone that no longer exists should not resolve."""
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Ghost": "out_deleted"}
            with self.assertRaises(ZoneLookupError):
                runtime.resolve_zone_name("Ghost")

    def test_ambiguous_fuzzy_match_raises_with_candidates(self):
        """Two zones both partial-matching the input → raise with the
        list of candidates so the user can disambiguate."""
        with _init_runtime() as runtime:
            runtime.roon_connection.api.zones = {
                "zone_sonos": {
                    "zone_id": "zone_sonos",
                    "display_name": "Kitchen Sonos",
                    "outputs": [{"output_id": "o_sonos", "display_name": "Kitchen Sonos"}],
                },
                "zone_echo": {
                    "zone_id": "zone_echo",
                    "display_name": "Kitchen Echo",
                    "outputs": [{"output_id": "o_echo", "display_name": "Kitchen Echo"}],
                },
            }
            with self.assertRaises(ZoneLookupError) as ctx:
                runtime.resolve_zone_name("kitchen")
            msg = str(ctx.exception)
            self.assertIn("ambiguous", msg.lower())
            self.assertIn("Kitchen Sonos", msg)
            self.assertIn("Kitchen Echo", msg)

    def test_exact_match_wins_over_ambiguous_partials(self):
        """If the input exactly matches a zone, that wins even when
        other zones contain it as a substring."""
        with _init_runtime() as runtime:
            runtime.roon_connection.api.zones["zone_extra"] = {
                "zone_id": "zone_extra",
                "display_name": "Kitchen Sonos",
                "outputs": [{"output_id": "o_extra", "display_name": "Kitchen Sonos"}],
            }
            self.assertEqual(runtime.resolve_zone_name("Kitchen"), "Kitchen")


# ── Zone alias context provider tests ────────────────────────────────────────


class TestZoneAliasContextProvider(unittest.TestCase):
    """Tests for the zone alias context provider in dynamic context."""

    def test_empty_when_no_aliases(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {}
            info = runtime.zone_aliases_provider.get_info()
            self.assertEqual(info, "")

    def test_returns_labelled_json_when_aliases_exist(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k", "Lounge": "out_lr"}
            info = runtime.zone_aliases_provider.get_info()
            self.assertTrue(info.startswith("Zone aliases:"))
            json_str = info.split(":", 1)[1].strip()
            parsed = json.loads(json_str)
            self.assertEqual(parsed["Potato"], "Kitchen")
            self.assertEqual(parsed["Lounge"], "Living Room")

    def test_provider_included_in_context_sections(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k"}
            sections = runtime.get_context_sections()
            titles = [s["title"] for s in sections]
            self.assertIn("Zone Aliases", titles)

    def test_provider_excluded_when_no_aliases(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {}
            sections = runtime.get_context_sections()
            titles = [s["title"] for s in sections]
            self.assertNotIn("Zone Aliases", titles)


# ── Tool-level alias resolution tests ────────────────────────────────────────


class TestActionToolAliasResolution(unittest.TestCase):
    """Tests that RoonActionTool resolves aliases before hitting roon_connection.

    The tool keeps the same _FakeRoonConnection (with real RoonZoneMixin
    on the call path) — alias resolution runs through production code,
    and the call shape at the connection boundary is asserted via the
    fake's playback_calls capture.
    """

    def test_alias_resolved_for_transport_action(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k"}
            tool = runtime.tool_registry._tools["roon_action"].tool_instance
            conn = tool.roon_connection  # the real _FakeRoonConnection

            from tools.roon_action import RoonActionToolInputSchema
            params = RoonActionToolInputSchema(action="play", zone="Potato")
            asyncio.run(tool.run_async(params))

            # Zone resolved to "Kitchen" before reaching the connection.
            self.assertEqual(len(conn.playback_calls), 1)
            self.assertEqual(conn.playback_calls[0]["zone"], "Kitchen")

    def test_unknown_zone_raises_in_action_tool(self):
        with _init_runtime() as runtime:
            tool = runtime.tool_registry._tools["roon_action"].tool_instance

            from tools.roon_action import RoonActionToolInputSchema
            params = RoonActionToolInputSchema(action="play", zone="Nonexistent")
            result = asyncio.run(tool.run_async(params))

            self.assertIn("FAILED", result.result)
            self.assertIn("Nonexistent", result.error)


class TestStatusToolAliasResolution(unittest.TestCase):
    """Tests that RoonStatusTool resolves aliases before hitting roon_connection."""

    def test_alias_resolved_for_playback_status(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k"}
            tool = runtime.tool_registry._tools["roon_status"].tool_instance
            conn = tool.roon_connection

            from tools.roon_status import RoonStatusToolInputSchema
            params = RoonStatusToolInputSchema(
                operation="get_zones_status", zone="Potato",
            )
            result = asyncio.run(tool.run_async(params))

            self.assertEqual(len(conn.zone_snapshot_calls), 1)
            self.assertEqual(conn.zone_snapshot_calls[0]["zone"], "Kitchen")
            self.assertIsNone(result.error)

    def test_unknown_zone_raises_in_status_tool(self):
        with _init_runtime() as runtime:
            tool = runtime.tool_registry._tools["roon_status"].tool_instance

            from tools.roon_status import RoonStatusToolInputSchema
            params = RoonStatusToolInputSchema(
                operation="get_zones_status", zone="Nonexistent",
            )
            result = asyncio.run(tool.run_async(params))

            self.assertIn("failed", result.result.lower())
            self.assertIn("Nonexistent", result.error)

    def test_none_zone_returns_all_zones(self):
        """When no zone is specified, get_zones_status returns all zones."""
        with _init_runtime() as runtime:
            tool = runtime.tool_registry._tools["roon_status"].tool_instance
            conn = tool.roon_connection

            from tools.roon_status import RoonStatusToolInputSchema
            params = RoonStatusToolInputSchema(
                operation="get_zones_status", zone=None,
            )
            result = asyncio.run(tool.run_async(params))

            self.assertGreaterEqual(conn.zones_snapshot_calls, 1)
            self.assertIn("Living Room", result.result)


# ── Config tool uses same resolution ─────────────────────────────────────────


class TestConfigToolUsesUnifiedResolution(unittest.TestCase):
    """Tests that perform_config_action resolves via the same path."""

    def test_set_default_zone_via_alias(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k"}
            result = runtime.perform_config_action("Set Default Zone", zone="Potato")
            self.assertIn("Kitchen", result)
            self.assertEqual(runtime.roon_connection.get_default_zone(), "Kitchen")

    def test_transfer_zone_via_alias(self):
        with _init_runtime() as runtime:
            runtime.zone_aliases = {"Potato": "out_k", "Den": "out_o"}
            result = runtime.perform_config_action(
                "Transfer Zone", zone="Potato", zone_to_transfer_to="Den",
            )
            self.assertIn("Kitchen", result)
            self.assertIn("Office", result)


if __name__ == "__main__":
    unittest.main()
