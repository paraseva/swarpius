import logging
from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import ZoneLookupError

_log = logging.getLogger("swarpius.zones")


class RoonZoneMixin:
    """Zone half of :class:`RoonConnection`. Not a standalone mixin —
    lives in its own module for navigability, composed only into
    :class:`RoonConnection` alongside the other Roon* mixins.

    Default-zone state: the user-facing ``target_zone`` is derived from
    ``_preferred_output_id`` (a stable Roon output handle) by finding
    the zone that currently contains that output. Group / ungroup /
    online-offline of the surrounding zone is followed automatically.
    """

    def refresh_zones_from_api(self) -> None:
        """Actively pull fresh zone state from Roon's /get_zones endpoint
        and merge into ``self.api.zones``.

        Workaround for python-roonapi: queue events don't update
        ``api.zones``, so after group/ungroup the cache stays on
        Roon's transient ``zones_changed`` (all stopped) indefinitely.
        """
        get_zones = getattr(self.api, "_get_zones", None)
        if not callable(get_zones):
            return
        try:
            fresh = get_zones()
        except Exception:
            _log.warning("refresh_zones_from_api: /get_zones failed", exc_info=True)
            return
        if not isinstance(fresh, dict):
            return
        for zone_id, zone in fresh.items():
            existing = self.api.zones.get(zone_id)
            if existing is not None:
                existing.update(zone)
            else:
                self.api.zones[zone_id] = zone

    @property
    def target_zone(self) -> Optional[str]:
        output_id = getattr(self, "_preferred_output_id", None)
        if not output_id:
            return None
        for zone in self.api.zones.values():
            for output in zone.get("outputs", []):
                if output.get("output_id") == output_id:
                    return zone.get("display_name")
        return None

    def _resolve_name_to_output_id(self, name: str) -> Optional[str]:
        """Resolve a display_name (zone or output) to an output_id.

        Zone display_name match wins over output display_name match.
        For a group zone we anchor on its first output (the "base").
        """
        if not name:
            return None
        name_lower = name.lower()
        for zone in self.api.zones.values():
            if zone.get("display_name", "").lower() == name_lower:
                outputs = zone.get("outputs", [])
                if outputs:
                    return outputs[0].get("output_id")
        for zone in self.api.zones.values():
            for output in zone.get("outputs", []):
                if output.get("display_name", "").lower() == name_lower:
                    return output.get("output_id")
        return None

    def _first_reported_output(self) -> Tuple[Optional[str], Optional[str]]:
        for zone in self.api.zones.values():
            for output in zone.get("outputs", []):
                output_id = output.get("output_id")
                if output_id:
                    return output_id, output.get("display_name")
        return None, None

    def _resolve_default_zone(self) -> None:
        """Seed ``_preferred_output_id`` once.

        A preferred output, once set, is preserved across an offline
        window so the user's choice survives a temporary zone
        disappearance (e.g. BT headphones going to standby). The
        frontend renders the offline state by reading the live zone
        list; the agent doesn't silently substitute.

        At boot (no preferred output yet) the configured
        ``_default_zone_name`` is resolved if it matches an online
        zone; otherwise the first reported output is adopted so the
        agent has something to operate on until the user picks.
        """
        if getattr(self, "_preferred_output_id", None):
            return

        name = getattr(self, "_default_zone_name", None)
        if name:
            output_id = self._resolve_name_to_output_id(name)
            if output_id:
                self._preferred_output_id = output_id
                self._preferred_zone_label = self.target_zone
                return

        first_output_id, first_name = self._first_reported_output()
        if first_output_id:
            self._preferred_output_id = first_output_id
            self._preferred_zone_label = self.target_zone
            if name:
                _log.warning(
                    "Default zone %r not found; falling back to %r",
                    name, first_name,
                )
            else:
                _log.info(
                    "No default zone set — adopting first reported output %r",
                    first_name,
                )
            return

        _log.warning(
            "No zones reported by Roon Core yet — default unresolved",
        )

    def _unknown_zone_error(self, zone_name: str) -> ZoneLookupError:
        """Build a ZoneLookupError that lists available zones."""
        zone_names = self.get_zone_names()
        return ZoneLookupError(
            f"Unknown zone '{zone_name}'. "
            f"Available zones: {', '.join(zone_names) if zone_names else 'none'}."
        )

    def _find_zone_by_name(self, zone_name: str) -> Optional[Dict[str, Any]]:
        """Case-insensitive zone lookup by display_name.

        The single source of truth for "does a zone with this name exist" —
        used by every lookup / snapshot / controls path. Returns the raw
        zone dict or None. Callers decide whether to raise, fall back, or
        extract a specific field.
        """
        target = zone_name.lower()
        for zone in self.api.zones.values():
            if zone.get("display_name", "").lower() == target:
                return zone
        return None

    def _get_zone(self, zone_name: str) -> Optional[str]:
        zone = self._find_zone_by_name(zone_name)
        return zone["display_name"] if zone else None

    def _lookup_output_id(self, zone_name: Optional[str] = None) -> str:

        zone_name = zone_name or self.target_zone

        if not zone_name:
            raise ZoneLookupError("No zone specified, and no default zone set.")

        zone = self._find_zone_by_name(zone_name)
        if zone:
            return zone["zone_id"]

        # Fall back to matching output display names — handles the case where
        # the target zone is an output that's now inside a group (e.g. default
        # zone "Chord Qutest" after it's been grouped into "Chord Qutest + 1").
        # The Roon browse API accepts output_ids in zone_or_output_id.
        for z in self.api.zones.values():
            for output in z.get("outputs", []):
                if output.get("display_name", "").lower() == zone_name.lower():
                    return output["output_id"]

        raise self._unknown_zone_error(zone_name)

    def _lookup_output_id_for_controls(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> str:
        if output:
            for output_item in self.api.outputs.values():
                if output_item["display_name"].lower() == output.lower():
                    return output_item["output_id"]
            output_names = [o.get("display_name") for o in self.api.outputs.values() if o.get("display_name")]
            raise ZoneLookupError(
                f"Unknown output '{output}'. Available outputs: {', '.join(output_names) if output_names else 'none'}."
            )

        zone_name = zone or self.target_zone
        if not zone_name:
            raise ZoneLookupError("No zone specified, and no default zone set.")

        resolved_zone = self._find_zone_by_name(zone_name)
        if not resolved_zone:
            raise self._unknown_zone_error(zone_name)
        if not resolved_zone.get("outputs"):
            raise ZoneLookupError(f"Zone '{zone_name}' exists but has no active outputs.")

        return resolved_zone["outputs"][0]["output_id"]

    def get_zone_snapshot(self, zone: Optional[str] = None) -> Dict[str, Any]:
        zone_name = zone or self.target_zone
        if not zone_name:
            raise ZoneLookupError("No zone specified, and no default zone set.")

        resolved_zone = self._find_zone_by_name(zone_name)
        if not resolved_zone:
            raise self._unknown_zone_error(zone_name)

        return dict(resolved_zone)

    def get_zones_snapshot(self) -> List[Dict[str, Any]]:
        return [dict(zone) for zone in self.api.zones.values()]

    def is_zone_grouped(self, zone_name: str) -> bool:
        """Whether this zone has multiple outputs (is a group)."""
        zone = self.get_zone_snapshot(zone_name)
        return len(zone.get("outputs", [])) > 1

    def get_grouped_output_names(self, zone_name: str) -> List[str]:
        """Return display names of all outputs in this zone."""
        zone = self.get_zone_snapshot(zone_name)
        return [o.get("display_name") for o in zone.get("outputs", [])]

    def get_zones_with_group_info(self) -> List[Dict[str, Any]]:
        """Return all zones with grouping metadata. Filters ghost zones."""
        result = []
        for zone in self.api.zones.values():
            outputs = zone.get("outputs", [])
            if not outputs:
                continue
            output_names = [o.get("display_name") for o in outputs]
            result.append({
                "display_name": zone.get("display_name"),
                "state": zone.get("state"),
                "zone_id": zone.get("zone_id"),
                "is_grouped": len(outputs) > 1,
                "group_members": output_names,
            })
        return result

    def get_zone_names(self) -> List[str]:
        return [zone.get("display_name") for zone in self.api.zones.values() if zone.get("display_name")]

    def get_zone_display_name(self, zone_name: str) -> Optional[str]:
        if not zone_name:
            return None
        zone = self._find_zone_by_name(zone_name)
        return zone.get("display_name") if zone else None

    def set_default_zone(self, zone_name: str) -> str:
        output_id = self._resolve_name_to_output_id(zone_name)
        if not output_id:
            raise self._unknown_zone_error(zone_name)
        self._preferred_output_id = output_id
        self._preferred_zone_label = self.target_zone
        return self.target_zone or zone_name

    def get_default_zone(self) -> Optional[str]:
        """User-facing default zone label. Follows live renames
        (grouping changes the zone's display_name) while the
        preferred output is reachable, and falls back to the
        last-known label when it's offline so the UI can render
        "Living Room (offline)" instead of a blank entry."""
        if not self.target_zone:
            self._resolve_default_zone()
        live = self.target_zone
        if live:
            self._preferred_zone_label = live
            return live
        return getattr(self, "_preferred_zone_label", None)

    def transfer_zone(self, from_zone: str, to_zone: str) -> None:
        from_zone_id = self._lookup_output_id(from_zone)
        to_zone_id = self._lookup_output_id(to_zone)
        self.api.transfer_zone(from_zone_id, to_zone_id)

    def get_queue_items(self, zone: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the raw queue items list for a zone, or empty list if unavailable."""
        zone_snapshot = self.get_zone_snapshot(zone=zone)
        zone_id = zone_snapshot.get("zone_id")
        event = self.last_queue_events_by_zone.get(zone_id)
        if not event:
            return []
        return event.get("data", {}).get("items", [])

    def get_queue_snapshot(self, zone: Optional[str] = None) -> Dict[str, Any]:
        zone_snapshot = self.get_zone_snapshot(zone=zone)
        zone_id = zone_snapshot.get("zone_id")
        return {
            "zone_id": zone_id,
            "display_name": zone_snapshot.get("display_name"),
            "state": zone_snapshot.get("state"),
            "queue_items_remaining": zone_snapshot.get("queue_items_remaining"),
            "now_playing": zone_snapshot.get("now_playing"),
            "latest_queue_event": self.last_queue_events_by_zone.get(zone_id) or self.last_queue_event,
        }

    def get_realtime_snapshot(self, zone: Optional[str] = None) -> Dict[str, Any]:
        zone_snapshot = self.get_zone_snapshot(zone=zone)
        zone_id = zone_snapshot.get("zone_id")
        latest_state_for_zone = None
        if self.last_state_event:
            for item in self.last_state_event.get("zones", []):
                if item.get("zone_id") == zone_id:
                    latest_state_for_zone = item
                    break
        return {
            "zone": zone_snapshot,
            "latest_state_event": self.last_state_event,
            "latest_state_for_zone": latest_state_for_zone,
            "latest_queue_event": self.last_queue_events_by_zone.get(zone_id) or self.last_queue_event,
        }

    def get_playing_zones_artwork_snapshot(self) -> List[Dict[str, Any]]:
        snapshots: List[Dict[str, Any]] = []
        for zone in self.api.zones.values():
            state = (zone.get("state") or "").lower()
            # Include paused zones on startup so the UI can render cards for
            # paused playback immediately without waiting for a new state event.
            if state not in {"playing", "paused"}:
                continue
            now_playing = zone.get("now_playing") or {}
            three_line = now_playing.get("three_line") or {}
            outputs = zone.get("outputs", [])
            snapshots.append(
                {
                    "zone_id": zone.get("zone_id"),
                    "display_name": zone.get("display_name"),
                    "state": zone.get("state"),
                    "seek_position": zone.get("seek_position"),
                    "image_key": now_playing.get("image_key"),
                    "outputs": outputs,
                    "now_playing": {
                        "line1": three_line.get("line1"),
                        "line2": three_line.get("line2"),
                        "line3": three_line.get("line3"),
                        "length": now_playing.get("length"),
                    },
                },
            )
        return snapshots
