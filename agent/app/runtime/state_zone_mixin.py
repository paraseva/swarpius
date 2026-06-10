"""Zone-domain methods on ``RuntimeState``.

Split out of ``state.py`` to keep the main file focused on
initialisation + composition. Almost everything here is a thin
delegation to ``self.zone_domain`` — the mixin is a clean way to
keep them in one themed file without changing RuntimeState's public
surface.

The class is composed into ``RuntimeState`` via inheritance (same
pattern as ``RoonConnection``'s mixins). Tests and callers see the
methods directly on the ``RuntimeState`` instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from app.roon.zone_domain import ZoneDomain

if TYPE_CHECKING:
    pass


class _StateZoneMixin:
    """Zone-state methods mixed into :class:`RuntimeState`.

    Composes the ``ZoneDomain`` API (aliases, cache, resolution,
    broadcasts) and the alias-persistence property surface. Tests and
    callers reach these via ``runtime.<method>`` exactly as before.
    """

    # ── Cross-references provided by RuntimeState ─────────────────
    # Read by methods below; populated in RuntimeState.__init__.
    zone_domain: ZoneDomain
    roon_connection: Any

    # Aliases / cache / persistence path — proxied to the
    # ``ZoneDomain`` instance so the dict identities stay stable for
    # tools that captured the underlying collections at registration.

    @property
    def zone_aliases(self) -> Dict[str, str]:
        return self.zone_domain.zone_aliases

    @zone_aliases.setter
    def zone_aliases(self, value: Dict[str, str]) -> None:
        self.zone_domain.zone_aliases.clear()
        self.zone_domain.zone_aliases.update(value)

    @property
    def _zone_cache(self) -> Dict[str, Dict[str, Any]]:
        return self.zone_domain._zone_cache

    @_zone_cache.setter
    def _zone_cache(self, value: Dict[str, Dict[str, Any]]) -> None:
        self.zone_domain._zone_cache.clear()
        self.zone_domain._zone_cache.update(value)

    # Persistence paths — proxied so tests can redirect to a tempdir
    # after RuntimeState is constructed (e.g. test_default_zone_broadcast).

    @property
    def zone_aliases_path(self):
        return self.zone_domain.zone_aliases_path

    @zone_aliases_path.setter
    def zone_aliases_path(self, value) -> None:
        self.zone_domain.zone_aliases_path = value

    # ── Context-provider helpers (read by RuntimeState init) ─────

    def _get_zone_aliases_context(self) -> str:
        return self.zone_domain.get_zone_aliases_context()

    def _get_zone_status_context(self) -> str:
        return self.zone_domain.get_zone_status_context()

    # ── Persistence + cache ──────────────────────────────────────

    def _load_zone_aliases(self) -> None:
        self.zone_domain.load_zone_aliases()

    def _save_zone_aliases(self) -> None:
        self.zone_domain.save_zone_aliases()

    def format_zone_label(self, zone_name: str) -> str:
        return self.zone_domain.format_zone_label(zone_name)

    def _build_zone_cache(self) -> Dict[str, Dict[str, Any]]:
        return self.zone_domain.build_zone_cache()

    def _reconcile_zone_state(self) -> None:
        self.zone_domain.reconcile_zone_state()

    # ── Grouping helpers ─────────────────────────────────────────

    def _resolve_zone_for_ungroup(self, target: str) -> str:
        return self.zone_domain.resolve_zone_for_ungroup(target)

    def _check_output_in_group(self, candidate: str) -> Optional[str]:
        return self.zone_domain.check_output_in_group(candidate)

    # ── Name resolution ──────────────────────────────────────────

    def resolve_zone_name(self, zone_or_alias: str) -> str:
        return self.zone_domain.resolve_zone_name(zone_or_alias)

    @staticmethod
    def _normalise_zone_key(value: str) -> str:
        return ZoneDomain._normalise_zone_key(value)

    def _resolve_zone_name_fuzzy(self, candidate: str) -> Optional[str]:
        return self.zone_domain.resolve_zone_name_fuzzy(candidate)

    def _get_alias_for_zone(self, zone_name: Optional[str]) -> Optional[str]:
        return self.zone_domain.get_alias_for_zone(zone_name)

    # ── Default-zone broadcasts ─────────────────────────────────

    def get_default_zone_payload(self) -> Dict[str, Any]:
        return self.zone_domain.get_default_zone_payload()

    def _broadcast_default_zone(self) -> None:
        self.zone_domain.broadcast_default_zone()

    def roon_core_status_for_connect(self) -> Optional[str]:
        """Core-status to send a freshly-connected client, or None to
        send nothing. Only meaningful once paired: before that the
        RoonSetup view owns the screen, and reporting "lost" here would
        flash the mid-session "Reconnecting to your Roon Core" overlay
        during first-run / restart."""
        if self.roon_state != "paired":
            return None
        return "connected" if (
            self.roon_connection and self.roon_connection.is_connected
        ) else "lost"

    def broadcast_roon_ready(self) -> None:
        """Re-push Roon-derived state (default zone + a zone snapshot)
        after a connection completes. A client that connected mid-pairing
        got an empty connect-time snapshot (roon_connection wasn't wired
        yet); this delivers the real state without a manual refresh."""
        self._broadcast_default_zone()
        try:
            self._emit_zone_snapshot_if_changed()
        except Exception:
            # The default zone is the load-bearing part for the UI; a
            # snapshot lookup hiccup shouldn't sink the whole broadcast.
            pass

    def _broadcast_zone_labels(self, zone_name: str) -> None:
        """Push a fresh zone snapshot so the frontend re-renders
        cards with the updated alias / group name."""
        if not self.roon_connection:
            return
        if zone_name == self.roon_connection.get_default_zone():
            self._broadcast_default_zone()
        try:
            self._emit_zone_snapshot_if_changed()
        except Exception:
            # Label re-broadcast is purely cosmetic — the alias / group
            # name change has already persisted. A snapshot lookup or
            # WS-send failure here just leaves the badge stale until
            # the next playback event re-broadcasts the zone naturally.
            pass
