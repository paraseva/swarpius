"""Internal helpers for :mod:`app.runtime_state`.

Small, self-contained pieces that the RuntimeState class composes but
doesn't need to define inline: the FIFO-bounded cache, the two state
locks' decorator wrappers, and the search-history entry record.  Kept
private to the runtime-state subsystem (the leading underscore stays
on the decorator names — existing callers import them from the parent
module via re-exports).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Optional


class _BoundedDict(dict):
    """Dict that evicts oldest entries (insertion order) on overflow.

    FIFO bound; caller sets a ``max_entries`` limit at construction.
    Suitable for simple caches where the cost of tracking recency
    isn't worth it — the bound stops unbounded growth, the eviction
    order is deterministic, and the behaviour is transparent to
    callers that treat it as a normal dict.
    """

    def __init__(self, max_entries: int) -> None:
        super().__init__()
        self._max_entries = max_entries

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        while len(self) > self._max_entries:
            oldest = next(iter(self))
            super().__delitem__(oldest)


def _locks_zone_state(method):
    """Decorator: serialise access to the zone-topology caches.

    Applied to RuntimeState methods that read or mutate
    ``zone_aliases`` / ``group_names`` / ``_zone_group_ids`` /
    ``_zone_cache``. Uses the instance's ``zone_state_lock`` (RLock),
    so nested calls within the class don't self-deadlock.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.zone_state_lock:
            return method(self, *args, **kwargs)
    return wrapper


def _locks_result_store(method):
    """Decorator: serialise access to result_store + search_history.

    Applied to RuntimeState methods that read or mutate the result
    store / search history pair. Under PARALLEL_TOOLS, multiple tool
    executions can call ``store_result_entries`` concurrently, which
    without serialisation races on the append+evict sequence. Uses an
    RLock so writers can call each other (store_result_entries calls
    store_result_handle).

    The contract — handle-in-history implies handle-in-store — is also
    defended by an internal reorder: when evicting, search_history is
    trimmed BEFORE the corresponding entries leave result_store, so an
    unsynchronised reader that snapshots search_history never sees a
    handle whose store entry has already been evicted.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.result_store_lock:
            return method(self, *args, **kwargs)
    return wrapper


@dataclass
class SearchHistoryEntry:
    """One entry in the search history cache shown to the coordinator."""

    result_handle: str
    tool_name: str
    description: str
    item_count: int
    timestamp_ms: int
    timestamp_display: str
    session_key: Optional[str] = None
