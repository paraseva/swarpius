"""Result store + search history manager.

Owns the handle-indexed result store, the recent-search-history list,
the monotonic counter, the last-minted handle, and the RLock that
serialises writes.

``RuntimeState`` aliases the mutable collections (``result_store``,
``search_history``, ``result_store_lock``) so tools that captured the
underlying dict/list by reference at registration keep working. The
scalar fields (``result_store_counter``, ``last_result_handle``) are
proxied via property on RuntimeState.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from app.runtime.result_store_types import ResultStoreEntry
from app.runtime.state_internals import SearchHistoryEntry
from app.settings import get_settings


class ResultStoreManager:
    """Thread-safe CRUD over result_store + search_history.

    All mutations use in-place updates so the mutable collection
    identities (the dict and the list) stay stable across the object's
    lifetime — any reference captured by a tool at registration time
    remains valid."""

    def __init__(self) -> None:
        # Shared-by-reference collections. Tools capture these at
        # registration; never reassign — in-place mutate.
        self.entries: Dict[str, Any] = {}
        self.history: List[SearchHistoryEntry] = []
        # Scalars. RuntimeState mirrors these via properties.
        self.counter: int = 0
        self.last_handle: Optional[str] = None
        # Shared lock. Callers that need atomic multi-step operations
        # can acquire it externally; all internal methods also acquire.
        self.lock = threading.RLock()

    # ── Direct CRUD ────────────────────────────────────────────────

    def update_entry(self, handle: str, payload: Any) -> None:
        """Overwrite (or insert) a handle's payload."""
        with self.lock:
            self.entries[handle] = payload

    def remove_entry(self, handle: str) -> None:
        """Drop a handle (does nothing if absent)."""
        with self.lock:
            self.entries.pop(handle, None)

    def find_history_entry_by_session(
        self, session_key: Optional[str],
    ) -> Optional[SearchHistoryEntry]:
        """Return the first history entry matching ``session_key`` (or None)."""
        if not session_key:
            return None
        with self.lock:
            for entry in self.history:
                if entry.session_key == session_key:
                    return entry
        return None

    # ── Handle minting + eviction ─────────────────────────────────

    def store_handle(self, payload: Any) -> str:
        """Mint a new result handle and store the payload.

        Direct-mint handles aren't added to ``history`` and so aren't
        evicted via that path — enforce a FIFO cap here (CPython dict
        insertion order) so long sessions don't leak memory."""
        with self.lock:
            self.counter += 1
            handle = f"res_{self.counter:05d}"
            self.entries[handle] = payload
            self.last_handle = handle
            self._enforce_cap()
            return handle

    def store_entries(self, entries: List[ResultStoreEntry]) -> List[str]:
        """Store a batch of result entries + register them in history.

        Drill-down entries update an existing history entry matched by
        session_key (or the most recent entry if no key). Returns one
        handle per input entry, in order.
        """
        handles: List[str] = []
        now_ms = int(time.time() * 1000)
        ts_display = time.strftime("%H:%M")

        with self.lock:
            for entry in entries:
                if entry.is_drill_down and self.history:
                    existing = self._find_history_entry_by_session_nolock(entry.session_key)
                    if not existing and not entry.session_key:
                        existing = self.history[-1]
                    if existing:
                        self.entries[existing.result_handle] = entry.items
                        existing.item_count = entry.item_count
                        handles.append(existing.result_handle)
                        continue

                self.counter += 1
                handle = f"res_{self.counter:05d}"
                self.entries[handle] = entry.items
                self.last_handle = handle
                self._enforce_cap()
                handles.append(handle)
                self.history.append(SearchHistoryEntry(
                    result_handle=handle,
                    tool_name=entry.tool_name,
                    description=entry.description,
                    item_count=entry.item_count,
                    timestamp_ms=now_ms,
                    timestamp_display=ts_display,
                    session_key=entry.session_key,
                ))

            cap = get_settings().search_history_max_entries
            if len(self.history) > cap:
                evicted = self.history[:-cap]
                # Trim history BEFORE removing from result_store so a
                # reader snapshotting history mid-eviction never sees a
                # handle whose store entry has already gone (defence in
                # depth on top of the lock held for the full call).
                del self.history[:-cap]
                for old in evicted:
                    self.entries.pop(old.result_handle, None)

        return handles

    def _find_history_entry_by_session_nolock(
        self, session_key: Optional[str],
    ) -> Optional[SearchHistoryEntry]:
        """Variant of find_history_entry_by_session for callers that
        already hold ``self.lock`` (avoids self-locking an RLock)."""
        if not session_key:
            return None
        for entry in self.history:
            if entry.session_key == session_key:
                return entry
        return None

    def _enforce_cap(self) -> None:
        """Internal: cap result_store at the locked settings limit.
        Caller must hold the lock."""
        excess = len(self.entries) - get_settings().result_store_max_entries
        if excess <= 0:
            return
        # Preserve handles referenced by the active history
        # (those are on their own eviction schedule).
        history_handles = {e.result_handle for e in self.history}
        droppable = [h for h in self.entries if h not in history_handles]
        for h in droppable[:excess]:
            self.entries.pop(h, None)
