"""Per-zone queue reference management.

Maintains a mapping between Roon queue_item_ids and random 5-char hex
references.  References are minted when items appear in the queue
(via subscription events) and invalidated when items are removed.
This replaces the per-fetch reference minting that treated queues
like immutable search results.
"""

import secrets
from typing import Dict, List, Optional, Tuple

# Maximum number of invalidated references to retain for informative errors.
_MAX_INVALIDATED = 200


def _item_description(item: dict) -> str:
    """Extract a human-readable description from a raw Roon queue item."""
    two_line = item.get("two_line", {})
    one_line = item.get("one_line", {})
    return two_line.get("line1") or one_line.get("line1", "")


class QueueReferenceMap:
    """Manages hex reference <-> queue_item_id mapping for a single zone's queue.

    References are minted when items first appear (via queue subscription
    events) and persist for the item's lifetime in the queue.  When an item
    is removed, its reference moves to an invalidated set so that stale
    lookups produce informative errors rather than generic "not found".
    """

    def __init__(self) -> None:
        self._refs: Dict[int, str] = {}        # queue_item_id -> hex_ref
        self._reverse: Dict[str, int] = {}     # hex_ref -> queue_item_id
        self._invalidated: Dict[str, str] = {}  # hex_ref -> item description

    def mint(self, queue_item_id: int) -> str:
        """Mint a new random 5-char hex reference for a queue item.

        If the item already has a reference, returns the existing one.
        """
        existing = self._refs.get(queue_item_id)
        if existing is not None:
            return existing
        hex_ref = secrets.token_hex(3)[:5]
        while hex_ref in self._reverse or hex_ref in self._invalidated:
            hex_ref = secrets.token_hex(3)[:5]
        self._refs[queue_item_id] = hex_ref
        self._reverse[hex_ref] = queue_item_id
        return hex_ref

    def invalidate(self, queue_item_id: int, description: str = "") -> None:
        """Move a reference to the invalidated set."""
        hex_ref = self._refs.pop(queue_item_id, None)
        if hex_ref is not None:
            del self._reverse[hex_ref]
            self._invalidated[hex_ref] = description
            self._trim_invalidated()

    def resolve(self, hex_ref: str) -> Tuple[Optional[int], Optional[str]]:
        """Resolve a hex reference.

        Returns ``(queue_item_id, None)`` if valid,
        ``(None, error_message)`` if invalidated or unknown.
        """
        qid = self._reverse.get(hex_ref)
        if qid is not None:
            return (qid, None)
        desc = self._invalidated.get(hex_ref)
        if desc is not None:
            label = f"'{desc}' " if desc else ""
            return (None, f"Queue item {label}has been removed from the queue")
        return (None, f"Unknown queue reference '{hex_ref}'")

    def get_ref(self, queue_item_id: int) -> Optional[str]:
        """Get the hex reference for a queue item, or ``None``."""
        return self._refs.get(queue_item_id)

    @property
    def active_refs(self) -> Dict[int, str]:
        """Current queue_item_id -> hex_ref mapping (copy)."""
        return dict(self._refs)

    def clear(self) -> None:
        """Clear all references (active and invalidated)."""
        self._refs.clear()
        self._reverse.clear()
        self._invalidated.clear()

    # ── Bulk operations (called from event handlers) ──────────────

    def reconcile_full_list(
        self,
        new_items: List[dict],
        old_items: Optional[List[dict]] = None,
    ) -> None:
        """Reconcile references against a complete queue item list.

        Called when a full list arrives (initial subscription, or queue
        replacement via "Play Now").  Preserves references for items that
        remain, invalidates those that disappeared, mints for new arrivals.
        """
        new_ids = {
            item["queue_item_id"]
            for item in new_items
            if "queue_item_id" in item
        }

        old_descs: Dict[int, str] = {}
        if old_items:
            for item in old_items:
                qid = item.get("queue_item_id")
                if qid is not None:
                    old_descs[qid] = _item_description(item)

        for qid in list(self._refs):
            if qid not in new_ids:
                self.invalidate(qid, old_descs.get(qid, ""))

        for item in new_items:
            qid = item.get("queue_item_id")
            if qid is not None and qid not in self._refs:
                self.mint(qid)

    def apply_inserts(self, items: List[dict]) -> None:
        """Mint references for newly inserted queue items."""
        for item in items:
            qid = item.get("queue_item_id")
            if qid is not None:
                self.mint(qid)

    def apply_removes(self, removed_items: List[dict]) -> None:
        """Invalidate references for removed queue items.

        ``removed_items`` must be the actual items being removed (captured
        from the cache *before* deletion), not the change operation dict.
        """
        for item in removed_items:
            qid = item.get("queue_item_id")
            if qid is not None:
                self.invalidate(qid, _item_description(item))

    # ── Internal ──────────────────────────────────────────────────

    def _trim_invalidated(self) -> None:
        if len(self._invalidated) > _MAX_INVALIDATED:
            excess = len(self._invalidated) - _MAX_INVALIDATED
            for key in list(self._invalidated)[:excess]:
                del self._invalidated[key]
