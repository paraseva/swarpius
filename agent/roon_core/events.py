import logging
import time
from typing import Any, Callable, Dict, List, Optional

from roon_core.queue_references import QueueReferenceMap

_log = logging.getLogger("roon.events")


class RoonEventsMixin:
    """Events half of :class:`RoonConnection`. Not a standalone mixin —
    lives in its own module for navigability, composed only into
    :class:`RoonConnection` alongside the other Roon* mixins. Owns
    ``last_state_event``, ``last_queue_event``, ``_event_listeners``
    and the queue caches that the zone/playback/browse halves read
    from."""

    def register_event_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._event_listeners.append(callback)

    def _emit_event(self, payload: Dict[str, Any]) -> None:
        for callback in self._event_listeners:
            try:
                callback(payload)
            except Exception:
                _log.warning("Event listener callback failed", exc_info=True)

    def _make_queue_callback(self, zone_id: str):
        """Create a queue event callback that injects the zone_id.

        Roon's queue subscription response contains only ``{"items": [...]}``,
        with no zone identification.  We capture the zone_id from the
        subscription call so ``_on_queue_event`` can store items per zone.
        """
        def _callback(data):
            if isinstance(data, dict) and "zone_id" not in data:
                data["zone_id"] = zone_id
            self._on_queue_event(data)
        return _callback

    def _apply_queue_changes(self, zone_id: str, changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply differential queue changes to the cached item list.

        Roon sends the full item list on initial subscription, then sends
        differential updates with ``{"changes": [...]}`` containing insert
        and remove operations (same pattern as node-roon-api-transport).
        References are reconciled inline: removed items are invalidated
        *before* deletion (to capture descriptions), inserts are minted after.
        """
        items = list(self._queue_items_cache.get(zone_id, []))
        ref_map = self._queue_ref_maps.get(zone_id)
        for change in changes:
            op = change.get("operation")
            index = change.get("index", 0)
            if op == "insert":
                new_items = change.get("items", [])
                items[index:index] = new_items
                if ref_map:
                    ref_map.apply_inserts(new_items)
            elif op == "remove":
                count = change.get("count", 0)
                if ref_map:
                    ref_map.apply_removes(items[index:index + count])
                del items[index:index + count]
        self._queue_items_cache[zone_id] = items
        return items

    def _ensure_queue_subscription(self, zone_id: str) -> None:
        """Subscribe to queue events for a zone, handling reconnects."""
        current_socket_id = id(self.api._roonsocket)
        if current_socket_id != self._queue_socket_id:
            self._queue_subscribed_zones.clear()
            self._queue_socket_id = current_socket_id
        if zone_id not in self._queue_subscribed_zones:
            self.api.register_queue_callback(self._make_queue_callback(zone_id), zone_id)
            self._queue_subscribed_zones.add(zone_id)

    def _on_state_event(self, event: str, changed_ids: List[str]) -> None:
        zones = []
        for zone_id in changed_ids:
            zone = self.api.zones.get(zone_id)
            if not zone:
                continue
            outputs = zone.get("outputs", [])
            zones.append(
                {
                    "zone_id": zone_id,
                    "display_name": zone.get("display_name"),
                    "state": zone.get("state"),
                    "seek_position": zone.get("seek_position"),
                    "queue_items_remaining": zone.get("queue_items_remaining"),
                    "queue_time_remaining": zone.get("queue_time_remaining"),
                    "settings": zone.get("settings"),
                    "now_playing": zone.get("now_playing"),
                    "outputs": outputs,
                    "is_grouped": len(outputs) > 1,
                    "group_members": [o.get("display_name") for o in outputs],
                },
            )
        payload = {
            "type": "state",
            "event": event,
            "changed_ids": changed_ids,
            "zones": zones,
            "timestamp": time.time(),
        }
        self.last_state_event = payload
        self._emit_event(payload)

        # Queue subscription lifecycle
        if event in ("zones_added", "zones_changed"):
            for zone_id in changed_ids:
                self._ensure_queue_subscription(zone_id)
        elif event == "zones_removed":
            for zone_id in changed_ids:
                self._queue_subscribed_zones.discard(zone_id)
                self.last_queue_events_by_zone.pop(zone_id, None)
                self._queue_items_cache.pop(zone_id, None)
                self._queue_ref_maps.pop(zone_id, None)

    def _get_or_create_ref_map(self, zone_id: str) -> QueueReferenceMap:
        ref_map = self._queue_ref_maps.get(zone_id)
        if ref_map is None:
            ref_map = QueueReferenceMap()
            self._queue_ref_maps[zone_id] = ref_map
        return ref_map

    def capture_queue_reference_maps(self) -> Dict[str, Any]:
        """Snapshot every zone's queue-reference map for persistence."""
        return {
            zone_id: ref_map.capture_state()
            for zone_id, ref_map in self._queue_ref_maps.items()
        }

    def restore_queue_reference_maps(self, data: Dict[str, Any]) -> None:
        """Rebuild the per-zone maps from a snapshot. Must run before the
        queue subscription's first ``reconcile_full_list`` so items still in
        the queue keep their original hex references rather than being
        re-minted."""
        for zone_id, map_data in data.items():
            self._get_or_create_ref_map(zone_id).restore_state(map_data)

    def get_queue_references(self, zone: Optional[str] = None) -> Optional[QueueReferenceMap]:
        """Get the queue reference map for a zone (by display name or zone_id)."""
        if zone is None:
            zone = getattr(self, "target_zone", None)
        if zone is None:
            return None
        if zone in self._queue_ref_maps:
            return self._queue_ref_maps[zone]
        for zid, zdata in self.api.zones.items():
            if zdata.get("display_name") == zone:
                return self._queue_ref_maps.get(zid)
        return None

    def resolve_queue_ref(self, hex_ref: str, zone: Optional[str] = None) -> int:
        """Resolve a 5-char hex queue reference to a queue_item_id.

        Searches all zone ref maps (or a specific zone if provided).
        Raises ValueError with an informative message if the reference
        is invalidated (item removed) or unknown.
        """
        maps_to_search = []
        if zone:
            ref_map = self.get_queue_references(zone=zone)
            if ref_map:
                maps_to_search.append(ref_map)
        else:
            maps_to_search.extend(self._queue_ref_maps.values())

        for ref_map in maps_to_search:
            qid, err = ref_map.resolve(hex_ref)
            if qid is not None:
                return qid
            if err and "removed" in err:
                raise ValueError(err)

        raise ValueError(
            f"Queue reference '{hex_ref}' not found. "
            f"Fetch the queue with get_queue_status first."
        )

    def _on_queue_event(self, data: Dict[str, Any]) -> None:
        zone_id = data.get("zone_id")
        if not zone_id:
            candidate = data.get("zone_or_output_id")
            if candidate in self.api.outputs:
                zone_id = self.api.outputs[candidate].get("zone_id")
            else:
                zone_id = candidate

        # Resolve items: full list on initial subscription, apply diffs after
        if "items" in data:
            items = data["items"]
            if zone_id:
                old_items = self._queue_items_cache.get(zone_id, [])
                ref_map = self._get_or_create_ref_map(zone_id)
                ref_map.reconcile_full_list(items, old_items=old_items)
                self._queue_items_cache[zone_id] = list(items)
        elif "changes" in data:
            if zone_id:
                self._get_or_create_ref_map(zone_id)
            items = self._apply_queue_changes(zone_id, data["changes"]) if zone_id else []
        else:
            items = self._queue_items_cache.get(zone_id, []) if zone_id else []

        normalised_data = dict(data)
        normalised_data["items"] = items

        payload = {
            "type": "queue",
            "zone_id": zone_id,
            "data": normalised_data,
            "timestamp": time.time(),
        }
        self.last_queue_event = payload
        if zone_id:
            self.last_queue_events_by_zone[zone_id] = payload
        self._emit_event(payload)

    def _ensure_live_subscriptions(self) -> None:
        if self._subscriptions_registered:
            return
        self.api.register_state_callback(self._on_state_event)
        for zone_id in self.api.zones:
            self._ensure_queue_subscription(zone_id)
        self._subscriptions_registered = True
