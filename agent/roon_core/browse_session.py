import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ItemIdentity:
    """Semantic fingerprint of a Roon item — survives key invalidation."""

    title: str
    subtitle: Optional[str] = None
    hint: Optional[str] = None
    image_key: Optional[str] = None


@dataclass
class SearchRecipe:
    """The sequence of steps that originally surfaced this item."""

    search_string: str
    category: Optional[str] = None
    parent_chain: List[ItemIdentity] = field(default_factory=list)


@dataclass
class StableReference:
    """A reference that can recover a live item_key across browse sessions."""

    ref_id: str
    identity: ItemIdentity
    recipe: SearchRecipe
    cached_item_key: Optional[str] = None
    roon_session_key: str = ""
    item_key_path: List[str] = field(default_factory=list)
    last_accessed: float = field(default_factory=time.monotonic)


class BrowseSessionManager:
    """Manages Roon browse sessions via multi_session_key and stable references."""

    def __init__(self, max_refs: int = 500, max_sessions: int = 16) -> None:
        self._session_prefix: str = secrets.token_hex(4)
        self._session_counter: int = 0
        self._max_sessions: int = max_sessions
        self.refs: Dict[str, StableReference] = {}
        self._ref_counter: int = 0
        self.max_refs: int = max_refs
        self._session_depth: Dict[str, int] = {}
        # Per-session navigation state — the items most recently loaded on
        # each browse session. Keeps drill/compile output isolated when
        # multiple sessions operate concurrently on the same connection.
        self._session_current_list: Dict[str, Any] = {}
        # Reserve the stop-marker session up-front so is_key_live works
        # for refs minted on it and so the round-robin slot pool never
        # picks it for an unrelated search.
        self._session_depth[self.stop_session_key] = 0

    @property
    def stop_session_key(self) -> str:
        """Dedicated, permanently-reserved session for the stop-marker
        coordinator. Outside the round-robin slot pool — never recycled
        by ``new_search_session`` — so the coordinator can keep a stable
        ``track_item_key`` cache valid across many stop dispatches."""
        return f"stop-{self._session_prefix}"

    def new_search_session(self) -> str:
        """Mint a multi_session_key for a fresh top-level search.

        Keys cycle through a fixed pool of ``max_sessions`` slots (default
        16) so the Roon Core doesn't accumulate unbounded session state.
        When a slot is reused, any refs pointing to the old session are
        purged — they would have stale item_keys.

        A random prefix unique to this manager instance prevents
        collisions with Roon Core's cached state from a previous process.
        """
        slot = self._session_counter % self._max_sessions
        self._session_counter += 1
        key = f"s-{self._session_prefix}-{slot:x}"
        if key in self._session_depth:
            self.refs = {
                ref_id: ref for ref_id, ref in self.refs.items()
                if ref.roon_session_key != key
            }
            self._session_current_list.pop(key, None)
        self._session_depth[key] = 0
        return key

    def get_session_depth(self, session_key: str) -> int:
        """Return the current browse depth for a session (0 = search root)."""
        return self._session_depth.get(session_key, 0)

    def set_session_depth(self, session_key: str, depth: int) -> None:
        """Set the browse depth for a session."""
        self._session_depth[session_key] = max(0, depth)

    def get_current_list(self, session_key: str) -> Optional[Any]:
        """Return the session's most recently loaded result, or None."""
        return self._session_current_list.get(session_key)

    def set_current_list(self, session_key: str, result: Any) -> None:
        """Record the session's most recently loaded result."""
        self._session_current_list[session_key] = result

    @property
    def action_session_key(self) -> str:
        return "action"

    @property
    def recovery_session_key(self) -> str:
        return "recovery"

    def find_existing_ref(
        self, session_key: str, item_key: Optional[str],
    ) -> Optional[StableReference]:
        """Find an existing ref for this item in the same browse session."""
        if not item_key:
            return None
        for ref in self.refs.values():
            if ref.roon_session_key == session_key and ref.cached_item_key == item_key:
                ref.last_accessed = time.monotonic()
                return ref
        return None

    def mint_ref(
        self,
        identity: ItemIdentity,
        recipe: SearchRecipe,
        item_key: Optional[str],
        session_key: str,
        item_key_path: Optional[List[str]] = None,
    ) -> str:
        """Create a new stable reference and return its ID."""
        while len(self.refs) >= self.max_refs:
            oldest = min(self.refs.values(), key=lambda r: r.last_accessed)
            del self.refs[oldest.ref_id]

        self._ref_counter += 1
        ref_id = secrets.token_hex(3)[:5]
        while ref_id in self.refs:
            ref_id = secrets.token_hex(3)[:5]

        self.refs[ref_id] = StableReference(
            ref_id=ref_id,
            identity=identity,
            recipe=SearchRecipe(
                search_string=recipe.search_string,
                category=recipe.category,
                parent_chain=list(recipe.parent_chain),
            ),
            cached_item_key=item_key,
            roon_session_key=session_key,
            item_key_path=list(item_key_path or []),
        )
        return ref_id

    def get_ref(self, ref_id: str) -> Optional[StableReference]:
        """Look up a reference and update its access time."""
        if ref_id.startswith("S:"):
            ref_id = ref_id[2:]
        ref = self.refs.get(ref_id)
        if ref:
            ref.last_accessed = time.monotonic()
        return ref

    def is_key_live(self, ref: StableReference) -> bool:
        """Check whether a reference's cached item_key is still valid.

        With multi_session_key isolation, item_keys remain valid on any
        session that hasn't been pop_all'd — not just the active session.
        """
        return (
            ref.roon_session_key in self._session_depth
            and ref.cached_item_key is not None
        )

    def update_ref_key(
        self,
        ref: StableReference,
        new_item_key: str,
        new_session_key: str,
        new_item_key_path: Optional[List[str]] = None,
    ) -> None:
        """Update a reference's cached key after successful recovery."""
        ref.cached_item_key = new_item_key
        ref.roon_session_key = new_session_key
        if new_item_key_path is not None:
            ref.item_key_path = list(new_item_key_path)
        ref.last_accessed = time.monotonic()
