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
