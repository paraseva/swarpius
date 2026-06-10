from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional


class ZoneSnapshotBuilder:
    """Builds the frontend-facing zone snapshot from the current
    Roon zones."""

    def __init__(
        self,
        get_alias: Callable[[Optional[str]], Optional[str]],
    ) -> None:
        self._get_alias = get_alias
        self._last_signature: Optional[str] = None

    def build(self, roon_zones: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            self._build_one(roon_zones[zone_id])
            for zone_id in sorted(roon_zones)
        ]

    def changed_since_last(self, snapshot: List[Dict[str, Any]]) -> bool:
        signature = json.dumps(snapshot, sort_keys=True)
        changed = signature != self._last_signature
        self._last_signature = signature
        return changed

    # ── Internal ──────────────────────────────────────────────────

    def _build_one(self, zone: Dict[str, Any]) -> Dict[str, Any]:
        raw_state = str(zone.get("state") or "").lower()
        queue_remaining = int(zone.get("queue_items_remaining") or 0)
        # Roon emits state="stopped" for paused zones whose wrapper
        # was torn down by a group/ungroup; the queue persists and
        # the track is resumable.
        if raw_state == "stopped" and queue_remaining > 0:
            state = "paused"
        else:
            state = raw_state

        now_playing = zone.get("now_playing") or {}
        three_line = now_playing.get("three_line") or {}
        image_key = (
            now_playing.get("image_key")
            if state in {"playing", "paused"} else None
        )

        outputs = zone.get("outputs") or []
        outputs_volume = [self._reshape_output(o) for o in outputs]

        settings = zone.get("settings") or {}

        return {
            "zone_id": zone.get("zone_id"),
            "display_name": zone.get("display_name"),
            "zone_alias": self._get_alias(zone.get("display_name")),
            "group_name": None,
            "state": state,
            "seek_position": zone.get("seek_position"),
            "queue_items_remaining": queue_remaining,
            "queue_time_remaining": zone.get("queue_time_remaining"),
            "shuffle": bool(settings.get("shuffle", False)),
            "loop": settings.get("loop") or "disabled",
            "auto_radio": bool(settings.get("auto_radio", False)),
            "is_grouped": len(outputs) > 1,
            "group_members": [o.get("display_name") for o in outputs],
            "outputs_volume": outputs_volume,
            "image_key": image_key,
            "now_playing": {
                "line1": three_line.get("line1"),
                "line2": three_line.get("line2"),
                "line3": three_line.get("line3"),
                "length": now_playing.get("length"),
            },
        }

    @staticmethod
    def _reshape_output(output: Dict[str, Any]) -> Dict[str, Any]:
        vol = output.get("volume") or {}
        return {
            "name": output.get("display_name"),
            "value": vol.get("value"),
            "type": vol.get("type"),
            "is_muted": vol.get("is_muted", False),
            "min": vol.get("min", 0),
            "max": vol.get("max", 100),
            "step": vol.get("step", 1),
        }
