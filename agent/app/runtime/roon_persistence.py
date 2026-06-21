"""Persistence participants for Roon-connection-scoped state.

These adapt the Roon connection's state to the PersistentState protocol.
They are attached once the connection exists (a later construction point
than the runtime), via ``RuntimeState.attach_roon_persistence``.
"""

from __future__ import annotations

from typing import Any, Dict


class QueueRefsState:
    """Persists the per-zone queue-reference maps (the Q:<hex> references
    the model is shown and later acts on). Delegates to the connection,
    which owns the maps."""

    state_key = "queue_refs"

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    def capture_state(self) -> Dict[str, Any]:
        return self._conn.capture_queue_reference_maps()

    def restore_state(self, data: Dict[str, Any]) -> None:
        self._conn.restore_queue_reference_maps(data)


class DefaultZoneState:
    """Persists the runtime default-zone choice (a stable Roon output id), so
    a "make the kitchen my default" survives a restart instead of reverting to
    the boot-time seed. ``_preferred_output_id`` / ``_preferred_zone_label``
    are the same attributes ``set_default_zone`` writes."""

    state_key = "default_zone"

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    def capture_state(self) -> Dict[str, Any]:
        return {
            "output_id": getattr(self._conn, "_preferred_output_id", None),
            "label": getattr(self._conn, "_preferred_zone_label", None),
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        output_id = data.get("output_id")
        if output_id:
            self._conn._preferred_output_id = output_id
            self._conn._preferred_zone_label = data.get("label")
