"""Deterministic save/restore of runtime state across restarts.

A :class:`PersistenceManager` holds the registered participants — the
modules whose in-memory state must survive a restart. Saving is *not* driven
by the LLM coordinator: :meth:`PersistenceManager.commit` is invoked by the
deterministic request-completion plumbing and writes every participant's
state in a single transaction (so the transcript + state snapshot commit
atomically on the shared connection). Restoring is read-once at startup into
a bag; each module applies its own slice at construction.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.io.state_db import StateDb

logger = logging.getLogger(__name__)


@runtime_checkable
class PersistentState(Protocol):
    """A module whose state is captured at request completion and restored
    at startup. ``capture_state`` must return a JSON-serialisable dict that
    ``restore_state`` can consume."""

    state_key: str

    def capture_state(self) -> Dict[str, Any]: ...

    def restore_state(self, data: Dict[str, Any]) -> None: ...


class PersistenceManager:
    """Registers participants, restores their state at startup, and commits
    it deterministically at request completion."""

    def __init__(self, state_db: StateDb) -> None:
        self._db = state_db
        self._participants: List[PersistentState] = []
        self._restored: Dict[str, Dict[str, Any]] = self._read_all()

    @property
    def state_db(self) -> StateDb:
        """The shared DB handle, for stores that persist outside the
        capture/restore snapshot (e.g. the listening-history table)."""
        return self._db

    def _read_all(self) -> Dict[str, Dict[str, Any]]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT state_key, payload FROM agent_state",
            ).fetchall()
        bag: Dict[str, Dict[str, Any]] = {}
        for state_key, payload in rows:
            try:
                bag[state_key] = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                # A corrupt row must not block restore of the others.
                logger.warning("Discarding unreadable saved state for %r", state_key)
        return bag

    def register(self, participant: PersistentState) -> None:
        """Add a participant to the save set. Call at the participant's
        construction, after it has applied its restored slice."""
        self._participants.append(participant)

    def restored_slice(self, state_key: str) -> Optional[Dict[str, Any]]:
        """Return the state saved for ``state_key`` by the previous run, or
        None if there is none."""
        return self._restored.get(state_key)

    def commit(self) -> None:
        """Persist every registered participant's current state in one
        transaction. If any participant's capture raises, the whole commit
        rolls back rather than leaving a half-written snapshot."""
        now_ms = int(time.time() * 1000)
        with self._db.transaction() as conn:
            for participant in self._participants:
                payload = json.dumps(participant.capture_state(), default=str)
                conn.execute(
                    "INSERT INTO agent_state (state_key, payload, updated_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(state_key) DO UPDATE SET "
                    "payload = excluded.payload, updated_at = excluded.updated_at",
                    (participant.state_key, payload, now_ms),
                )
