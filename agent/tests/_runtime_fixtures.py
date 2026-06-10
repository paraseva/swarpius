"""Shared bare-RuntimeState fixtures for unit tests.

Most zone / artwork / control / result-store tests construct a
minimal RuntimeState via ``object.__new__`` and wire only the
attributes they exercise. Centralising the wiring keeps each
subsystem's attribute set in one place.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

from roon_core.zones import RoonZoneMixin


def wire_zone_domain(rs, tmp_path: Optional[Path] = None) -> tempfile.TemporaryDirectory | None:
    """Attach a ZoneDomain to ``rs`` with paths redirected to a tempdir.

    Returns the TemporaryDirectory object so the caller can keep it
    alive for the test's lifetime (pass ``tmp_path`` to skip creation).
    """
    from app.roon.zone_domain import ZoneDomain

    td = None
    if tmp_path is None:
        td = tempfile.TemporaryDirectory()
        tmp_path = Path(td.name)

    # ws_send callback — allow rs to lack _ws_send_callback for now
    def _ws_send(channel: str, payload: Any) -> None:
        cb = getattr(rs, "_ws_send_callback", None)
        if cb:
            cb(channel, payload)

    from app.roon.zone_artwork_service import ZoneArtworkCache
    from app.runtime.zones import ZoneSubsystem

    # Tests bypass RuntimeState.__init__ via object.__new__, so the
    # zones subsystem isn't pre-built. Build one here with the
    # tempdir-backed domain and a minimal artwork cache.
    rs.zones = ZoneSubsystem(
        domain=ZoneDomain(
            zone_aliases_path=tmp_path / "zone_aliases.json",
            get_roon_connection=lambda: getattr(rs, "roon_connection", None),
            ws_send=_ws_send,
        ),
        artwork=ZoneArtworkCache(max_entries=10),
    )
    return td


def wire_result_store(rs) -> None:
    from app.runtime.result_store_manager import ResultStoreManager
    rs.results = ResultStoreManager()
    rs.result_store = rs.results.entries
    rs.search_history = rs.results.history
    rs.result_store_lock = rs.results.lock


def wire_zone_artwork(rs) -> None:
    """Replace the zone subsystem's artwork cache with one sized
    from settings. ``wire_zone_domain`` must have run first."""
    from app.roon.zone_artwork_service import ZoneArtworkCache
    from app.settings import get_settings

    rs.zones.replace_artwork(ZoneArtworkCache(
        max_entries=get_settings().image_cache_max_entries,
    ))


def wire_roon_control(rs) -> None:
    from app.roon.control_service import RoonControlService
    rs.roon_control = RoonControlService(
        roon_connection_getter=lambda: rs.roon_connection,
        resolve_zone_name=lambda z: rs.resolve_zone_name(z),
        get_alias_for_zone=lambda z: rs._get_alias_for_zone(z),
        broadcast_default_zone=lambda: rs._broadcast_default_zone(),
    )


def wire_config_action(rs) -> None:
    """Attach a ConfigActionService. Requires ``rs.zone_domain`` first."""
    from app.roon.config_action_service import ConfigActionService
    rs.config_action = ConfigActionService(
        zone_domain=rs.zone_domain,
        get_roon_connection=lambda: rs.roon_connection,
        broadcast_zone_labels=lambda z: rs._broadcast_zone_labels(z),
    )


class WSCapture:
    """Records ``(channel, payload)`` pairs so tests can assert the
    contract of WebSocket emissions: which channel fired, with what
    payload shape. Use as a drop-in replacement for ``ws_send_fn`` /
    ``_ws_send_callback`` in unit tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, channel: str, payload: Any, **_kwargs: Any) -> None:
        self.calls.append((channel, payload))

    def payloads_on(self, channel: str) -> list[Any]:
        return [p for c, p in self.calls if c == channel]

    def channels(self) -> list[str]:
        return [c for c, _ in self.calls]


def wire_ws_test_bus(capture: "WSCapture", runtime: Any) -> Any:
    """Helper: build an EventBus, subscribe a WsBroadcaster that emits
    via ``capture``. Tests that previously relied on auto-wiring
    inside ``process_request`` use this to recreate the same flow
    explicitly."""
    from app.coordinator.event_bus import EventBus
    from app.io.ws_broadcaster import WsBroadcaster
    bus = EventBus()
    broadcaster = WsBroadcaster(ws_send_fn=capture, runtime=runtime)
    bus.subscribe(broadcaster.handle)
    return bus


def wire_cli_test_bus(
    rich_console: Optional[Any] = None,
    tts_say_fn: Optional[Any] = None,
    on_request_complete: Optional[Any] = None,
    show_request_ids: bool = False,
) -> Any:
    """Helper: build an EventBus, subscribe a CliRenderer with the
    provided side-effect callbacks."""
    from app.cli.renderer import CliRenderer
    from app.coordinator.event_bus import EventBus
    bus = EventBus()
    renderer = CliRenderer(
        rich_console=rich_console if rich_console is not None else MagicMock(),
        tts_say_fn=tts_say_fn,
        on_request_complete=on_request_complete,
        show_request_ids=show_request_ids,
    )
    bus.subscribe(renderer.handle)
    return bus


def make_request_runtime(extra_env: Optional[dict] = None):
    """Minimal initialised RuntimeState for ``process_request`` tests.

    Real tool registry + LLM client wiring; Roon + skill-loading patched
    so tests don't touch them. ``ENABLE_DIAGNOSTIC_AGENT`` is pinned off
    so a developer's local .env can't change per-call behaviour for
    shared-runtime tests — tests that need a flag on should override
    via ``extra_env``.
    """
    import os
    from unittest.mock import patch

    from app.runtime.state import RuntimeState

    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
        "ENABLE_DIAGNOSTIC_AGENT": "false",
    }
    if extra_env:
        env.update(extra_env)

    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", MagicMock),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("", ""),
        ),
    ):
        rs = RuntimeState()
        rs.ensure_initialised()
    return rs


class _MockRoonConnection(RoonZoneMixin):
    """RoonConnection stand-in for shared test fixtures.

    Inherits ``RoonZoneMixin`` so the real zone-resolution methods
    (``_find_zone_by_name``, ``get_zone_display_name``, ``get_zone_names``,
    ``is_zone_grouped``, ``get_zones_snapshot``, ``get_zones_with_group_info``,
    ``get_zone_snapshot``) execute on the call path. Only the Roon API
    surface (``api.zones`` / ``api.outputs`` data) and the transport
    calls (``group_zones``, ``ungroup_zones``, ``transfer_zone``,
    ``set_default_zone``) are stubbed.

    ``api.zones`` is mutable on the instance so tests can simulate live
    Roon events between calls.

    ``target_zone`` is overridden as a plain settable attribute so tests
    can directly express "the default zone is X" without needing to
    arrange a matching ``_preferred_output_id``.
    """

    # Override RoonZoneMixin's read-only `target_zone` property with a
    # plain attribute. Tests assign to it directly to express the
    # default-zone state without going through the
    # _preferred_output_id → output → zone resolution chain.
    target_zone: Optional[str] = None

    def __init__(
        self,
        zones: dict,
        outputs: Optional[dict] = None,
        target_zone: Optional[str] = None,
    ) -> None:
        self.api = SimpleNamespace(zones=zones, outputs=outputs or {})
        self._default_zone_name: Optional[str] = target_zone
        self._preferred_output_id: Optional[str] = (
            self._resolve_name_to_output_id(target_zone) if target_zone else None
        )
        self.target_zone = target_zone
        # Transport-call recorders / stubs (boundaries on RoonConnection
        # that aren't on RoonZoneMixin).
        self.group_zones = MagicMock()
        self.ungroup_zones = MagicMock()
        self.transfer_zone = MagicMock()

    def set_default_zone(self, name: str) -> str:
        """Override the mixin to mirror its side effect into the plain
        ``target_zone`` attribute too — tests read ``target_zone``
        directly to assert on the post-call state."""
        from app.exceptions import ZoneLookupError
        dn = self.get_zone_display_name(name)
        if not dn:
            raise ZoneLookupError(f"Unknown zone {name}")
        self.target_zone = dn
        self._default_zone_name = dn
        out = self._resolve_name_to_output_id(dn)
        if out:
            self._preferred_output_id = out
        return dn


def make_mock_roon_connection(
    zones: dict,
    outputs: Optional[dict] = None,
    target_zone: Optional[str] = None,
):
    """Mock RoonConnection serving zone/output data via the real
    ``RoonZoneMixin`` accessor surface.

    Tests pass mutable ``zones`` and can swap data mid-test to simulate
    Roon events; ``RoonZoneMixin.*`` reads ``api.zones`` dynamically.
    """
    return _MockRoonConnection(
        zones=zones, outputs=outputs, target_zone=target_zone,
    )


def bare_runtime_for_zone_tests(
    tmp_path: Optional[Path] = None,
    with_connection: bool = False,
) -> tuple[Any, tempfile.TemporaryDirectory | None]:
    """Convenience factory: a bare RuntimeState with zone_domain,
    zone_artwork, roon_control and a discard ws_send_callback.  Skips
    result_store (most zone tests don't need it)."""
    from app.roon.zone_snapshot import ZoneSnapshotBuilder
    from app.runtime.state import RuntimeState

    rs = object.__new__(RuntimeState)
    rs.roon_connection = MagicMock() if with_connection else None
    rs._ws_send_callback = MagicMock()
    td = wire_zone_domain(rs, tmp_path)
    wire_zone_artwork(rs)
    wire_roon_control(rs)
    # Late-binding lookup so tests can swap _get_alias_for_zone after construction.
    rs.zone_snapshot = ZoneSnapshotBuilder(
        get_alias=lambda name: rs._get_alias_for_zone(name),
    )
    return rs, td
