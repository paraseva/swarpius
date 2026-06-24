import secrets
import threading
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


def _dump_identity(identity: ItemIdentity) -> Dict[str, Any]:
    return {
        "title": identity.title,
        "subtitle": identity.subtitle,
        "hint": identity.hint,
        "image_key": identity.image_key,
    }


def _load_identity(data: Dict[str, Any]) -> ItemIdentity:
    return ItemIdentity(
        title=data["title"],
        subtitle=data.get("subtitle"),
        hint=data.get("hint"),
        image_key=data.get("image_key"),
    )


def _dump_ref(ref: StableReference) -> Dict[str, Any]:
    return {
        "ref_id": ref.ref_id,
        "identity": _dump_identity(ref.identity),
        "recipe": {
            "search_string": ref.recipe.search_string,
            "category": ref.recipe.category,
            "parent_chain": [_dump_identity(i) for i in ref.recipe.parent_chain],
        },
        "cached_item_key": ref.cached_item_key,
        "roon_session_key": ref.roon_session_key,
        "item_key_path": list(ref.item_key_path),
    }


def _load_ref(data: Dict[str, Any]) -> StableReference:
    recipe = data["recipe"]
    return StableReference(
        ref_id=data["ref_id"],
        identity=_load_identity(data["identity"]),
        recipe=SearchRecipe(
            search_string=recipe["search_string"],
            category=recipe.get("category"),
            parent_chain=[_load_identity(i) for i in recipe.get("parent_chain", [])],
        ),
        cached_item_key=data.get("cached_item_key"),
        roon_session_key=data.get("roon_session_key", ""),
        item_key_path=list(data.get("item_key_path", [])),
    )


class BrowseSessionManager:
    """Manages Roon browse sessions via multi_session_key and stable references."""

    # Persistence participant key (structurally satisfies PersistentState).
    state_key = "browse_refs"

    def __init__(self, max_refs: int = 500, max_sessions: int = 128) -> None:
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
        # Sessions currently reserved by an in-flight operation (see
        # ``acquire``). Tool calls run on separate threads under
        # PARALLEL_TOOLS, so reservation must be atomic — hence the lock.
        self._lock = threading.RLock()
        self._in_use: set[str] = set()
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
        128) so the Roon Core doesn't accumulate unbounded session state.
        When a slot is reused, refs pointing to the old session have their
        cached binding invalidated (their item_keys are stale on the Core) but
        are kept, so they can be re-established from their recipe — ref
        lifetime (``max_refs`` LRU) is decoupled from session lifetime.

        A random prefix unique to this manager instance prevents
        collisions with Roon Core's cached state from a previous process.
        """
        with self._lock:
            slot = self._session_counter % self._max_sessions
            self._session_counter += 1
            key = f"s-{self._session_prefix}-{slot:x}"
            if key in self._session_depth:
                for ref in self.refs.values():
                    if ref.roon_session_key == key:
                        ref.cached_item_key = None
                self._session_current_list.pop(key, None)
            self._session_depth[key] = 0
            return key

    def acquire(self, session_key: str) -> str:
        """Reserve a session for the duration of one browse operation.

        Free → reserve it and return it unchanged (the fast path:
        independent searches and sequential drills never contend). Already
        reserved by a concurrent operation → lease a fresh session and
        return that instead, so the two never share one Roon browse cursor.
        The caller re-establishes its context on the returned session when it
        differs from the one requested.
        """
        with self._lock:
            if session_key not in self._in_use:
                self._in_use.add(session_key)
                return session_key
            fresh = self.new_search_session()
            self._in_use.add(fresh)
            return fresh

    def release(self, session_key: str) -> None:
        """Release a session reserved by :meth:`acquire`."""
        with self._lock:
            self._in_use.discard(session_key)

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

    # ── Persistence ────────────────────────────────────────────────

    def capture_state(self) -> Dict[str, Any]:
        """Snapshot the whole pool so a restart resumes exactly where it
        left off — the slot counter, per-session depth, every reference,
        and the current-list pagination state. ``last_accessed`` is
        process-relative (``time.monotonic``) and so is re-stamped on
        restore rather than persisted."""
        return {
            "session_prefix": self._session_prefix,
            "session_counter": self._session_counter,
            "session_depth": dict(self._session_depth),
            "refs": {ref_id: _dump_ref(ref) for ref_id, ref in self.refs.items()},
            "current_list": {
                session_key: result.model_dump(mode="json")
                for session_key, result in self._session_current_list.items()
                if hasattr(result, "model_dump")
            },
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        """Replace the pool with a previously captured snapshot."""
        from roon_core.schemas import RoonCoreResultsSchema

        self._session_prefix = data["session_prefix"]
        self._session_counter = data["session_counter"]
        self._session_depth.clear()
        self._session_depth.update(data.get("session_depth", {}))
        self.refs.clear()
        for ref_id, ref_data in data.get("refs", {}).items():
            self.refs[ref_id] = _load_ref(ref_data)
        self._session_current_list.clear()
        for session_key, result_data in data.get("current_list", {}).items():
            self._session_current_list[session_key] = RoonCoreResultsSchema.model_validate(
                result_data,
            )
