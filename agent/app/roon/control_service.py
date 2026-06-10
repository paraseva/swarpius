"""Roon control action dispatch.

Dispatches frontend / websocket control requests (play, pause, seek,
volume, mute, play-from-here, list-zones, set-default-zone) to the
underlying RoonConnection so RuntimeState doesn't carry the
per-action branches.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from app.exceptions import (
    RoonConnectionUnavailableError,
    UnsupportedActionError,
    ZoneLookupError,
)


class RoonControlService:
    """Action dispatcher wired with callbacks for the zone-naming and
    default-zone concerns that live on RuntimeState.
    """

    def __init__(
        self,
        roon_connection_getter: Callable[[], Any],
        resolve_zone_name: Callable[[Optional[str]], str],
        get_alias_for_zone: Callable[[Optional[str]], Optional[str]],
        broadcast_default_zone: Callable[[], None],
        stop_marker_coordinator_getter: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._get_connection = roon_connection_getter
        self._resolve_zone = resolve_zone_name
        self._get_alias = get_alias_for_zone
        self._broadcast_default = broadcast_default_zone
        self._get_stop_coordinator = stop_marker_coordinator_getter

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        connection = self._get_connection()
        if not connection:
            raise RoonConnectionUnavailableError("Roon connection is not available")

        action = str(payload.get("action") or "").strip().lower()
        if not action:
            raise ValueError("action is required")

        if action == "list_zones":
            return self._handle_list_zones(connection)
        if action == "set_default_zone":
            return self._handle_set_default_zone(connection, payload)
        if action == "set_volume":
            output_name = str(payload.get("output") or "").strip()
            if not output_name:
                return {"ok": False, "action": action, "error": "output name is required"}
            volume_value = int(payload.get("volume", 0))
            connection.set_volume_absolute(volume=volume_value, output=output_name)
            return {"ok": True, "action": action, "output": output_name, "volume": volume_value}
        if action == "mute":
            output_name = str(payload.get("output") or "").strip()
            if not output_name:
                return {"ok": False, "action": action, "error": "output name is required"}
            mute_state = payload.get("mute", True)
            connection.mute(mute=bool(mute_state), output=output_name)
            return {"ok": True, "action": action, "output": output_name, "mute": bool(mute_state)}

        if action == "play_from_here":
            queue_item_id = payload.get("queue_item_id")
            if queue_item_id is None:
                return {"ok": False, "action": action, "error": "queue_item_id is required"}
            zone_input = (payload.get("zone") or "").strip() or None
            zone = self._resolve_zone(zone_input) if zone_input else None
            connection.play_from_here(queue_item_id=int(queue_item_id), zone=zone)
            return {"ok": True, "action": action, "zone": zone, "queue_item_id": int(queue_item_id)}

        zone_input = (payload.get("zone") or "").strip() or None
        zone = self._resolve_zone(zone_input) if zone_input else None

        if action == "seek":
            if "position_seconds" not in payload:
                raise ValueError("position_seconds is required for seek action")
            position_seconds = int(payload["position_seconds"])
            if position_seconds < 0:
                raise ValueError("position_seconds must be >= 0")
            connection.seek(seconds=position_seconds, method="absolute", zone=zone)
            return {
                "ok": True,
                "action": action,
                "zone": zone,
                "position_seconds": position_seconds,
            }

        if action == "stop":
            # Routes through the StopMarkerCoordinator so the WS button
            # and the LLM tool share the same cached-state + recovery
            # path. ``use_pause_fallback`` covers disabled mode and
            # missing-marker scenarios — silently degrade to pause so
            # the user's stop intent still translates to "playback
            # halts" without a "feature unavailable" banner.
            coord = (
                self._get_stop_coordinator()
                if self._get_stop_coordinator else None
            )
            if coord is None:
                connection.playback_control(control="pause", zone=zone)
                return {"ok": True, "action": action, "zone": zone}
            result = coord.dispatch_stop(zone=zone)
            if result.use_pause_fallback:
                connection.playback_control(control="pause", zone=zone)
                return {"ok": True, "action": action, "zone": zone}
            if result.succeeded:
                return {"ok": True, "action": action, "zone": zone}
            return {
                "ok": False, "action": action, "zone": zone,
                "error": result.error,
            }

        control_map = {
            "play": "play",
            "pause": "pause",
            "next": "next",
            "previous": "previous",
        }
        control = control_map.get(action)
        if not control:
            raise UnsupportedActionError(f"Unsupported control action '{action}'")

        connection.playback_control(control=control, zone=zone)
        return {"ok": True, "action": action, "zone": zone}

    def _handle_list_zones(self, connection: Any) -> Dict[str, Any]:
        zones_info = connection.get_zones_with_group_info()
        default_zone = connection.get_default_zone()

        result = []
        for zone in zones_info:
            display_name = zone.get("display_name", "")
            zone_alias = self._get_alias(display_name)
            group_name = None

            # The default zone is stored as an output name which may differ
            # from the grouped zone's display name (e.g. "Chord Qutest" vs
            # "Chord Qutest + 1") — match on members too.
            is_default = False
            if default_zone:
                if display_name.lower() == default_zone.lower():
                    is_default = True
                else:
                    for member in zone.get("group_members", []):
                        if member.lower() == default_zone.lower():
                            is_default = True
                            break

            result.append({
                "display_name": display_name,
                "zone_alias": zone_alias,
                "group_name": group_name,
                "state": zone.get("state"),
                "is_default": is_default,
                "is_grouped": zone.get("is_grouped", False),
                "group_members": zone.get("group_members", []),
            })

        # Sort: default first, then playing, paused, stopped, then alphabetical
        state_order = {"playing": 0, "paused": 1, "loading": 2, "stopped": 3}
        result.sort(key=lambda z: (
            0 if z["is_default"] else 1,
            state_order.get(z.get("state", ""), 4),
            (z.get("group_name") or z.get("zone_alias") or z.get("display_name", "")).lower(),
        ))

        return {"ok": True, "action": "list_zones", "zones": result}

    def _handle_set_default_zone(
        self, connection: Any, payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        zone_input = (payload.get("zone") or "").strip()
        if not zone_input:
            return {"ok": False, "action": "set_default_zone", "error": "Zone name is required"}
        try:
            resolved = self._resolve_zone(zone_input)
            connection.set_default_zone(resolved)
            self._broadcast_default()
            return {"ok": True, "action": "set_default_zone", "zone": resolved}
        except (ZoneLookupError, RoonConnectionUnavailableError) as exc:
            return {"ok": False, "action": "set_default_zone", "zone": zone_input, "error": str(exc)}
