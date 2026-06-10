"""Zone domain: aliases, group names, zone-id↔group mappings, name
resolution (fuzzy + alias + group), reconciliation against live Roon
state, default-zone broadcast, and the context providers' zone
summaries.

``RuntimeState`` holds an instance as ``runtime.zone_domain`` plus
property proxies (``zone_aliases`` / ``_zone_cache``) and a direct
handle to ``zone_state_lock``. The proxies preserve dict identity on
re-assignment so tests can write ``rs.zone_aliases = {...}`` without
breaking captures held by tools.
"""

from __future__ import annotations

import difflib
import functools
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.constants import CHANNEL_DEFAULT_ZONE_UPDATE
from app.exceptions import RoonConnectionUnavailableError, ZoneLookupError
from app.roon.zone_formatting import build_compact_zone_status

_log = logging.getLogger("swarpius.zone_domain")


def _locks_zone_state(method):
    """Decorator bound to ZoneDomain's ``zone_state_lock`` attribute."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.zone_state_lock:
            return method(self, *args, **kwargs)
    return wrapper


class ZoneDomain:
    """Owns zone-naming state + the reconciliation / resolution logic."""

    def __init__(
        self,
        zone_aliases_path: Path,
        get_roon_connection: Callable[[], Any],
        ws_send: Callable[[str, Any], None],
        get_last_played_dict: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.zone_aliases: Dict[str, str] = {}
        # Per-alias last-known zone display_name. Surfaced to the LLM in
        # listings so an alias whose underlying zone is currently offline
        # still shows its name rather than a placeholder. Reflects the
        # zone the anchor output is in *right now* (changes with grouping).
        self._alias_display_cache: Dict[str, str] = {}
        # Per-alias last-known *output* display_name. Used for disk
        # persistence — stable across grouping changes (only renames in
        # Roon move it). Auto-saved when a resolve detects a name change.
        self._alias_output_name_cache: Dict[str, str] = {}
        self._zone_cache: Dict[str, Dict[str, Any]] = {}  # zone_id -> {display_name, outputs}
        # Last (resolved_display_name, is_online) we broadcast for the
        # default zone. None on first observation — no broadcast then;
        # the WS connect snapshot has already covered the initial state.
        self._last_default_zone_state: Optional[Tuple[Optional[str], bool]] = None
        # RLock so in-class methods can nest without self-deadlocking.
        self.zone_state_lock = threading.RLock()
        self.zone_aliases_path = zone_aliases_path
        self._get_connection = get_roon_connection
        self._ws_send = ws_send
        self._get_last_played_dict = get_last_played_dict

    # ── Context provider content ──────────────────────────────────

    @_locks_zone_state
    def get_zone_aliases_context(self) -> str:
        if not self.zone_aliases:
            return ""
        resolved = {
            alias: self._resolve_alias_to_display_name(alias)
            or self._alias_display_cache.get(alias, alias)
            for alias in self.zone_aliases
        }
        return "Zone aliases: " + json.dumps(resolved)

    def build_reverse_aliases(self) -> Dict[str, str]:
        """Return ``{display_name: alias}`` for the formatter. The
        domain stores aliases keyed alias → output, so we resolve each
        alias back to its current display name."""
        result: Dict[str, str] = {}
        for alias in self.zone_aliases:
            zone_name = self._resolve_alias_to_display_name(alias)
            if zone_name:
                result[zone_name] = alias
        return result

    @_locks_zone_state
    def get_zone_status_context(self) -> str:
        connection = self._get_connection()
        if not connection:
            return ""
        try:
            zones = connection.get_zones_snapshot()
        except Exception:
            return ""
        last_played_by_zone_id = self._build_last_played_map(zones)
        return build_compact_zone_status(
            zones,
            self.build_reverse_aliases(),
            connection.get_default_zone(),
            last_played_by_zone_id=last_played_by_zone_id,
        )

    def _build_last_played_map(
        self, zones: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        # History is per-output. Grouped outputs share an identical
        # deque by construction (the handler pushes the same entry to
        # every member), so any output of the zone gives the same
        # result — pick the first.
        if self._get_last_played_dict is None:
            return None
        result: Dict[str, Dict[str, Any]] = {}
        for zone in zones:
            zone_id = zone.get("zone_id")
            outputs = zone.get("outputs") or []
            if not zone_id or not outputs:
                continue
            first = outputs[0] if isinstance(outputs[0], dict) else None
            output_id = first.get("output_id") if first else None
            if not output_id:
                continue
            entry = self._get_last_played_dict(output_id)
            if entry:
                result[zone_id] = entry
        return result

    # ── Persistence ───────────────────────────────────────────────

    def load_zone_aliases(self) -> None:
        """On-disk format is human-readable ``{alias: output_name}``. We
        resolve each name to a stable output_id at load time so
        subsequent grouping / renames are followed dynamically. Both
        the display cache and the output-name cache are seeded from
        the disk value as fallbacks for when the anchor output is
        currently offline."""
        self.zone_aliases.clear()
        self._alias_display_cache.clear()
        self._alias_output_name_cache.clear()
        try:
            raw = self.zone_aliases_path.read_text(encoding="utf-8").strip()
            parsed = json.loads(raw) if raw else {}
        except Exception:
            return
        if not isinstance(parsed, dict):
            return
        connection = self._get_connection()
        has_zones = bool(getattr(getattr(connection, "api", None), "zones", None))
        for alias, value in parsed.items():
            if not alias or not value:
                continue
            alias_str = str(alias)
            value_str = str(value)
            if not has_zones:
                self.zone_aliases[alias_str] = value_str
                self._alias_display_cache[alias_str] = value_str
                self._alias_output_name_cache[alias_str] = value_str
                continue
            output_id = self._coerce_alias_value_to_output_id(value_str)
            if output_id:
                self.zone_aliases[alias_str] = output_id
            else:
                # Anchor output isn't visible right now (zone offline,
                # speakers off, etc.). Keep the alias with the saved
                # display_name as a placeholder; _resolve_alias_to_display_name
                # promotes it to an output_id when the zone reappears.
                self.zone_aliases[alias_str] = value_str
            self._alias_display_cache[alias_str] = value_str
            self._alias_output_name_cache[alias_str] = value_str

    def _coerce_alias_value_to_output_id(self, value: str) -> Optional[str]:
        connection = self._get_connection()
        if not connection:
            return None
        zones = getattr(getattr(connection, "api", None), "zones", None) or {}
        for zone in zones.values():
            for output in zone.get("outputs", []):
                if output.get("output_id") == value:
                    return value
        target_lower = value.lower()
        for zone in zones.values():
            for output in zone.get("outputs", []):
                if output.get("display_name", "").lower() == target_lower:
                    return output.get("output_id")
        for zone in zones.values():
            if zone.get("display_name", "").lower() == target_lower:
                outputs = zone.get("outputs", [])
                if len(outputs) == 1:
                    return outputs[0].get("output_id")
        return None

    def save_zone_aliases(self) -> None:
        """Write ``{alias: output_name}`` to disk. The output's
        display_name is stable across grouping (a zone's display_name
        changes when it gets grouped; the output's doesn't), so the
        on-disk format is robust to topology changes between sessions.
        Falls back to the raw anchor only if there's no cache entry."""
        serialised: Dict[str, str] = {}
        for alias, value in self.zone_aliases.items():
            serialised[alias] = self._alias_output_name_cache.get(alias, value)
        self.zone_aliases_path.write_text(
            json.dumps(serialised, indent=4),
            encoding="utf-8",
        )

    # ── Labels + lookups ─────────────────────────────────────────

    @_locks_zone_state
    def format_zone_label(self, zone_name: str) -> str:
        alias = self.get_alias_for_zone(zone_name)
        if alias:
            return f"{alias} ({zone_name})"
        return zone_name

    @_locks_zone_state
    def get_alias_for_zone(self, zone_name: Optional[str]) -> Optional[str]:
        if not zone_name:
            return None
        connection = self._get_connection()
        if not connection:
            return self._cached_alias_for_zone(zone_name)
        target = zone_name.lower()
        target_output_ids: set[str] = set()
        matched_display: Optional[str] = None
        for zone in connection.api.zones.values():
            if zone.get("display_name", "").lower() == target:
                matched_display = zone.get("display_name")
                for output in zone.get("outputs", []):
                    output_id = output.get("output_id")
                    if output_id:
                        target_output_ids.add(output_id)
                break
        if not target_output_ids:
            # Zone isn't currently visible to Roon (offline). The
            # alias survives across offline gaps via the display cache.
            return self._cached_alias_for_zone(zone_name)
        for alias, output_id in self.zone_aliases.items():
            if output_id in target_output_ids:
                if matched_display:
                    self._alias_display_cache[alias] = matched_display
                return alias
        return None

    def _cached_alias_for_zone(self, zone_name: str) -> Optional[str]:
        target = zone_name.lower()
        for alias, cached in self._alias_display_cache.items():
            if cached.lower() == target:
                return alias
        return None

    def _resolve_alias_to_display_name(self, alias: str) -> Optional[str]:
        canonical_alias = None
        stored_value = None
        for key, value in self.zone_aliases.items():
            if key.lower() == alias.lower():
                canonical_alias = key
                stored_value = value
                break
        if not stored_value or canonical_alias is None:
            return None
        connection = self._get_connection()
        if not connection:
            return None
        for zone in connection.api.zones.values():
            for output in zone.get("outputs", []):
                if output.get("output_id") == stored_value:
                    zone_display = zone.get("display_name")
                    output_name = output.get("display_name")
                    if zone_display:
                        self._alias_display_cache[canonical_alias] = zone_display
                    if output_name:
                        previous = self._alias_output_name_cache.get(canonical_alias)
                        self._alias_output_name_cache[canonical_alias] = output_name
                        if previous is not None and previous != output_name:
                            try:
                                self.save_zone_aliases()
                            except Exception:
                                _log.debug("Ignoring non-fatal failure while saving zone aliases", exc_info=True)
                    return zone_display
        # Fallback: the stored value is a display_name placeholder
        # (the anchor output was offline at load time). Try to find an
        # output whose display_name matches and promote the alias to
        # the proper output_id anchor.
        promoted_id = self._coerce_alias_value_to_output_id(stored_value)
        if promoted_id:
            self.zone_aliases[canonical_alias] = promoted_id
            try:
                self.save_zone_aliases()
            except Exception:
                _log.debug("Ignoring non-fatal failure while saving zone aliases", exc_info=True)
            for zone in connection.api.zones.values():
                for output in zone.get("outputs", []):
                    if output.get("output_id") == promoted_id:
                        zone_display = zone.get("display_name")
                        if zone_display:
                            self._alias_display_cache[canonical_alias] = zone_display
                        output_name = output.get("display_name")
                        if output_name:
                            self._alias_output_name_cache[canonical_alias] = output_name
                        return zone_display
        return None

    @_locks_zone_state
    def resolve_alias(self, alias: str) -> Optional[str]:
        """Live resolver: alias → display_name of the zone currently
        containing the anchor. Returns None if the alias isn't set, or
        if its zone is currently offline. Used by action dispatch
        where the caller needs to know whether a real action is
        possible right now."""
        return self._resolve_alias_to_display_name(alias)

    @_locks_zone_state
    def get_alias_display_name(self, alias: str) -> Optional[str]:
        """Display-friendly name for an alias. Returns the live zone
        display_name when available, else the last-known cached
        value. Returns None only if the alias doesn't exist."""
        live = self._resolve_alias_to_display_name(alias)
        if live:
            return live
        for key, cached in self._alias_display_cache.items():
            if key.lower() == alias.lower():
                return cached
        return None

    # ── Zone-cache snapshot + reconciliation ─────────────────────

    def build_zone_cache(self) -> Dict[str, Dict[str, Any]]:
        connection = self._get_connection()
        cache: Dict[str, Dict[str, Any]] = {}
        for zone_id, zone in connection.api.zones.items():
            outputs = zone.get("outputs", [])
            cache[zone_id] = {
                "display_name": zone.get("display_name", ""),
                "outputs": {
                    o.get("output_id", ""): o.get("display_name", "")
                    for o in outputs
                },
            }
        return cache

    @_locks_zone_state
    def reconcile_zone_state(self) -> None:
        """Update internal cache + broadcast default-zone transitions."""
        connection = self._get_connection()
        if not connection:
            return

        new_cache = self.build_zone_cache()

        current_target = connection.target_zone
        default_online_now = (
            bool(connection._find_zone_by_name(current_target))
            if current_target else False
        )
        current_state: Tuple[Optional[str], bool] = (current_target, default_online_now)
        state_changed = (
            self._last_default_zone_state is not None
            and current_state != self._last_default_zone_state
        )
        self._last_default_zone_state = current_state

        self._zone_cache = new_cache

        if state_changed:
            self.broadcast_default_zone()

    # ── Zone name resolution ─────────────────────────────────────

    def resolve_zone_for_ungroup(self, target: str) -> str:
        """Like resolve_zone_name but resolves an output name to its
        containing zone (instead of raising the output-in-group error)."""
        connection = self._get_connection()
        if not connection:
            raise RoonConnectionUnavailableError("Roon connection is not available")
        resolved = self.resolve_zone_name_fuzzy(target.strip())
        if resolved:
            return resolved
        for z in connection.api.zones.values():
            outputs = z.get("outputs", [])
            if len(outputs) <= 1:
                continue
            for output in outputs:
                if output.get("display_name", "").lower() == target.strip().lower():
                    return z.get("display_name")
        raise ZoneLookupError(
            f"Could not find a zone matching '{target}'. "
            "Provide a zone name or zone alias.",
        )

    @_locks_zone_state
    def resolve_zone_name(self, zone_or_alias: str) -> str:
        """Resolve a zone name or alias to a canonical Roon zone display
        name. If the input matches a member of a currently-grouped zone,
        resolves to that group's display_name — targeting a member
        addresses the group it's in."""
        connection = self._get_connection()
        if not connection:
            raise RoonConnectionUnavailableError("Roon connection is not available")

        candidate = zone_or_alias.strip()
        if not candidate:
            raise ZoneLookupError("Zone name is empty.")

        resolved = self.resolve_zone_name_fuzzy(candidate)
        if resolved:
            return resolved

        target_lower = candidate.lower()
        for zone in connection.api.zones.values():
            for output in zone.get("outputs", []):
                if output.get("display_name", "").lower() == target_lower:
                    return zone.get("display_name")

        raise ZoneLookupError(
            f"Unknown zone or alias '{zone_or_alias}'. "
            "Use a valid Roon zone name or configured alias.",
        )

    @staticmethod
    def _normalise_zone_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    @_locks_zone_state
    def resolve_zone_name_fuzzy(self, candidate: str) -> Optional[str]:
        connection = self._get_connection()
        if not connection:
            return None

        normalised_candidate = self._normalise_zone_key(candidate)
        if not normalised_candidate:
            return None

        zone_names = connection.get_zone_names()
        if not zone_names:
            return None

        alias_to_zone_display: Dict[str, str] = {}
        for alias in self.zone_aliases:
            resolved_zone = self._resolve_alias_to_display_name(alias)
            if resolved_zone:
                alias_to_zone_display[alias] = resolved_zone

        # 1) Exact case-insensitive match on real zone names.
        for zone_name in zone_names:
            if zone_name.lower() == candidate.lower():
                return zone_name

        # 2) Exact case-insensitive alias match.
        for alias, mapped_zone in alias_to_zone_display.items():
            if alias.lower() == candidate.lower():
                return mapped_zone

        # 3) Unique contains/prefix match across zones and aliases.
        zone_contains = [
            zone_name
            for zone_name in zone_names
            if normalised_candidate in self._normalise_zone_key(zone_name)
        ]
        alias_contains = [
            mapped_zone
            for alias, mapped_zone in alias_to_zone_display.items()
            if normalised_candidate in self._normalise_zone_key(alias)
        ]
        contains_matches = list(dict.fromkeys(zone_contains + alias_contains))
        if len(contains_matches) == 1:
            return contains_matches[0]
        if len(contains_matches) > 1:
            options = ", ".join(repr(m) for m in contains_matches)
            raise ZoneLookupError(
                f"'{candidate}' is ambiguous: matches {options}. "
                "Use the full name to disambiguate."
            )

        # 4) Token overlap + similarity scoring as a final fallback.
        candidate_tokens = set(normalised_candidate.split())
        best_zone: Optional[str] = None
        best_score = 0.0
        second_best_score = 0.0

        for zone_name in zone_names:
            normalised_zone = self._normalise_zone_key(zone_name)
            zone_tokens = set(normalised_zone.split())
            overlap_score = (
                len(candidate_tokens & zone_tokens) / len(candidate_tokens)
                if candidate_tokens
                else 0.0
            )
            similarity = difflib.SequenceMatcher(None, normalised_candidate, normalised_zone).ratio()
            score = (0.7 * overlap_score) + (0.3 * similarity)
            if score > best_score:
                second_best_score = best_score
                best_score = score
                best_zone = zone_name
            elif score > second_best_score:
                second_best_score = score

        if best_zone and best_score >= 0.65 and (best_score - second_best_score) >= 0.08:
            return best_zone

        return None

    # ── Default zone broadcast ────────────────────────────────────

    def get_default_zone_payload(self) -> Dict[str, Any]:
        connection = self._get_connection()
        zone_name = connection.get_default_zone() if connection else None
        is_grouped = False
        is_online = False
        if zone_name and connection:
            is_online = bool(connection._find_zone_by_name(zone_name))
            try:
                is_grouped = connection.is_zone_grouped(zone_name)
            except Exception:
                # is_grouped defaults to False above; if the lookup
                # races a Roon Core hiccup we just report ungrouped.
                pass
        return {
            "zone_name": zone_name,
            "alias": self.get_alias_for_zone(zone_name),
            "group_name": None,
            "is_grouped": is_grouped,
            "is_online": is_online,
        }

    def broadcast_default_zone(self) -> None:
        self._ws_send(CHANNEL_DEFAULT_ZONE_UPDATE, self.get_default_zone_payload())
