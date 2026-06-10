"""Config action dispatch for zone-related user intents.

Dispatcher for ``perform_config_action`` — routes "Set Default Zone",
"Set Zone Alias", "Group Zones", "Rename Group", etc. through the
zone domain + Roon connection + cross-service broadcasters.

Holds no state of its own; all mutations go through the injected
ZoneDomain so persistence, reconciliation, and the zone-state lock
all stay single-source-of-truth.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from app.exceptions import (
    RoonConnectionUnavailableError,
    UnsupportedActionError,
)
from app.roon.zone_domain import ZoneDomain


class ConfigActionService:
    """Runs user-initiated zone config actions against the domain +
    Roon connection, acquiring the zone-state lock for the full call
    so read/validate/write sequences stay atomic under concurrent
    Roon-dispatched reconciliation events."""

    def __init__(
        self,
        zone_domain: ZoneDomain,
        get_roon_connection: Callable[[], Any],
        broadcast_zone_labels: Callable[[str], None],
    ) -> None:
        self._domain = zone_domain
        self._get_connection = get_roon_connection
        self._broadcast_zone_labels = broadcast_zone_labels

    @staticmethod
    def _resolve_target_to_output(connection: Any, target: str) -> tuple:
        """Resolve a zone or member name to
        ``(output_id, output_display_name, zone_display_name)``.

        Zone-name match wins. If the target matches a zone display_name
        with >1 members, raises — aliasing grouped zones isn't
        supported. If the target matches a member name, returns
        regardless of whether that member is currently inside a group.
        """
        if not target:
            raise ValueError("Empty target name.")
        target_lower = target.lower()
        zones = getattr(connection.api, "zones", {})
        for zone in zones.values():
            if zone.get("display_name", "").lower() == target_lower:
                outputs = zone.get("outputs", [])
                if len(outputs) != 1:
                    raise ValueError(
                        f"'{target}' is currently a grouped zone. "
                        "Aliasing grouped zones isn't supported.",
                    )
                output = outputs[0]
                return (
                    output.get("output_id"),
                    output.get("display_name"),
                    zone.get("display_name"),
                )
        for zone in zones.values():
            for output in zone.get("outputs", []):
                if output.get("display_name", "").lower() == target_lower:
                    return (
                        output.get("output_id"),
                        output.get("display_name"),
                        zone.get("display_name"),
                    )
        raise ValueError(f"No zone named '{target}'.")

    @staticmethod
    def _name_collides_with_zone_or_output(connection: Any, name: str) -> bool:
        if not name:
            return False
        target = name.lower()
        zones = getattr(connection.api, "zones", {})
        for zone in zones.values():
            if zone.get("display_name", "").lower() == target:
                return True
            for output in zone.get("outputs", []):
                if output.get("display_name", "").lower() == target:
                    return True
        return False

    def perform(
        self,
        action: str,
        zone: Optional[str] = None,
        zone_to_transfer_to: Optional[str] = None,
        alias: Optional[str] = None,
        group_zones: Optional[List[str]] = None,
        new_name: Optional[str] = None,
    ) -> str:
        connection = self._get_connection()
        if not connection:
            raise RoonConnectionUnavailableError("Roon connection is not available")

        # Outer-level lock: preserves the original
        # @_locks_zone_state contract that the whole dispatch was atomic.
        # ZoneDomain's per-method locks are reentrant (RLock).
        with self._domain.zone_state_lock:
            return self._dispatch(
                connection, action, zone, zone_to_transfer_to,
                alias, group_zones, new_name,
            )

    def _dispatch(
        self,
        connection: Any,
        action: str,
        zone: Optional[str],
        zone_to_transfer_to: Optional[str],
        alias: Optional[str],
        group_zones: Optional[List[str]],
        new_name: Optional[str],
    ) -> str:
        domain = self._domain
        match action:
            case "Set Default Zone":
                resolved_zone = domain.resolve_zone_name(zone)
                connection.set_default_zone(resolved_zone)
                domain.broadcast_default_zone()
                zone_alias = domain.get_alias_for_zone(resolved_zone)
                return (
                    f"Default zone set to '{resolved_zone}'"
                    + (f" (aliased as '{zone_alias}')" if zone_alias else "")
                )
            case "Set Zone Alias":
                if not zone or not alias:
                    raise ValueError("Both zone and alias must be provided for Set Zone Alias action")
                output_id, output_name, output_zone_display = self._resolve_target_to_output(
                    connection, zone,
                )
                if self._name_collides_with_zone_or_output(connection, alias):
                    raise ValueError(
                        f"'{alias}' is already a zone or output name. Choose a different alias.",
                    )
                for existing in domain.zone_aliases:
                    if existing.lower() == alias.lower():
                        existing_resolved = domain.resolve_alias(existing) or "<offline>"
                        raise ValueError(
                            f"Alias '{alias}' already exists (currently maps to "
                            f"'{existing_resolved}'). Remove or rename it first, "
                            "or choose a different name.",
                        )
                domain.zone_aliases[alias] = output_id
                domain._alias_display_cache[alias] = output_zone_display
                if output_name:
                    domain._alias_output_name_cache[alias] = output_name
                domain.save_zone_aliases()
                self._broadcast_zone_labels(output_zone_display)
                return f"Zone alias '{alias}' set for '{output_zone_display}'"
            case "Remove Zone Alias":
                if not alias:
                    raise ValueError("Alias must be provided for Remove Zone Alias action")
                removed_key = None
                for existing_alias in domain.zone_aliases:
                    if existing_alias.lower() == alias.lower():
                        removed_key = existing_alias
                        break
                if not removed_key:
                    raise ValueError(f"Alias '{alias}' not found in zone aliases")
                removed_zone_display = domain.get_alias_display_name(removed_key)
                domain.zone_aliases.pop(removed_key, None)
                domain._alias_display_cache.pop(removed_key, None)
                domain._alias_output_name_cache.pop(removed_key, None)
                domain.save_zone_aliases()
                if removed_zone_display:
                    self._broadcast_zone_labels(removed_zone_display)
                return f"Zone alias '{removed_key}' removed"
            case "Clear All Zone Aliases":
                domain.zone_aliases.clear()
                domain._alias_display_cache.clear()
                domain._alias_output_name_cache.clear()
                domain.save_zone_aliases()
                domain.broadcast_default_zone()
                return "All zone aliases cleared"
            case "Get Default Zone":
                default_zone = connection.get_default_zone()
                if not default_zone:
                    return "No default zone set"
                zone_alias = domain.get_alias_for_zone(default_zone)
                return (
                    f"Default zone is '{default_zone}'"
                    + (f" (aliased as '{zone_alias}')" if zone_alias else " not aliased")
                )
            case "Transfer Zone":
                if not zone_to_transfer_to:
                    raise ValueError("zone_to_transfer_to must be provided for Transfer Zone action")
                from_zone = domain.resolve_zone_name(
                    zone or connection.get_default_zone(),
                )
                to_zone = domain.resolve_zone_name(zone_to_transfer_to)
                connection.transfer_zone(from_zone, to_zone)
                return f"Playback transferred from '{from_zone}' to '{to_zone}'."
            case "Get Zone Aliases":
                if not domain.zone_aliases:
                    return "No zone aliases set."
                lines = []
                for a in domain.zone_aliases:
                    lines.append(f"{a}: {domain.get_alias_display_name(a) or a}")
                return "Zone aliases:\n" + "\n".join(lines)
            case "Rename Zone Alias":
                if not alias or not new_name:
                    raise ValueError("Current alias and new name must be provided")
                old_key = None
                for existing_alias in domain.zone_aliases:
                    if existing_alias.lower() == alias.lower():
                        old_key = existing_alias
                        break
                if not old_key:
                    raise ValueError(f"Zone alias '{alias}' not found")
                output_id = domain.zone_aliases.pop(old_key)
                domain.zone_aliases[new_name] = output_id
                cached_display = domain._alias_display_cache.pop(old_key, None)
                if cached_display:
                    domain._alias_display_cache[new_name] = cached_display
                cached_output_name = domain._alias_output_name_cache.pop(old_key, None)
                if cached_output_name:
                    domain._alias_output_name_cache[new_name] = cached_output_name
                domain.save_zone_aliases()
                resolved_display = domain.get_alias_display_name(new_name) or new_name
                self._broadcast_zone_labels(resolved_display)
                return f"Zone alias renamed from '{old_key}' to '{new_name}' (maps to '{resolved_display}')"
            case "Group Zones":
                if not group_zones or len(group_zones) < 2:
                    raise ValueError("At least two zone names required for Group Zones action")
                connection.group_zones(group_zones)
                return f"Grouped zones: {', '.join(group_zones)}"
            case "Ungroup Zones":
                target = alias or zone
                if not target:
                    raise ValueError(
                        "Provide a group name, zone name, zone alias, or output name to ungroup.",
                    )
                resolved = domain.resolve_zone_for_ungroup(target)
                zone_snapshot = connection.get_zone_snapshot(resolved)
                outputs = zone_snapshot.get("outputs", [])
                if len(outputs) <= 1:
                    raise ValueError(f"Zone '{resolved}' is not grouped.")
                output_names = [o.get("display_name") for o in outputs]
                connection.ungroup_zones(output_names)
                return f"Ungrouped '{resolved}' (members: {', '.join(output_names)})"
            case "Get Groups":
                zones_info = connection.get_zones_with_group_info()
                grouped = [z for z in zones_info if z.get("is_grouped")]
                if not grouped:
                    return "No zones are currently grouped."
                lines = []
                for z in grouped:
                    display = z.get("display_name", "")
                    members = z.get("group_members", [])
                    lines.append(f"{display}: {', '.join(members)}")
                return "Currently grouped zones:\n" + "\n".join(lines)
            case _:
                raise UnsupportedActionError(f"Unknown config action '{action}'")
        # Defensive — every case above returns or raises, so this is
        # unreachable. Kept so static analysers see an explicit
        # terminator and don't infer an implicit ``return None``.
        raise AssertionError(f"unreachable: unhandled action '{action}'")
