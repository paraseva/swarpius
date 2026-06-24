"""Stateful browse fake — models one Roon browse cursor per ``multi_session_key``.

`tests/_browse_fake.py` stubs ``browse_core`` *statelessly* (keyed off item_key,
ignoring the session), so it cannot reproduce anything that depends on the Roon
Core's per-session cursor — including the parallel-sibling-drilldown collision.

This fake stubs the lower boundary instead — ``api.browse_browse`` /
``api.browse_load`` — and keeps a per-session cursor (a stack of levels) over a
navigable tree. So the real ``browse_core``, ``drill_down``,
``resolve_reference``, ``_position_session`` and recovery logic all run on the
call path, and two operations on *different* session keys are genuinely
isolated, while two on the *same* key share one cursor — the property the fix
turns on.

Build a tree with ``node(...)`` and register it for a search query via
``install_search(query, roots)``. item_keys are assigned ``"<level>:<pos>"`` so
the position suffix is stable across sessions (as production's
``_find_key_by_position`` requires).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from roon_core.browse import RoonBrowseMixin
from roon_core.browse_session import BrowseSessionManager


class _Node:
    __slots__ = ("title", "subtitle", "image_key", "hint", "children", "item_key")

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        image_key: Optional[str] = None,
        hint: str = "list",
        children: Optional[List["_Node"]] = None,
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        self.image_key = image_key
        self.hint = hint
        self.children = children or []
        self.item_key = ""  # assigned by _StatefulApi.install_search


def node(
    title: str,
    subtitle: str = "",
    image_key: Optional[str] = None,
    hint: str = "list",
    children: Optional[List[_Node]] = None,
) -> _Node:
    return _Node(title, subtitle, image_key, hint, children)


class _StatefulApi:
    """Per-session cursor over the tree. Each session's cursor is a stack of
    levels (lists of ``_Node``); the top of the stack is the current level."""

    def __init__(self) -> None:
        self.zones: Dict[str, Any] = {}
        self._search: Dict[str, List[_Node]] = {}
        self._stacks: Dict[str, List[List[_Node]]] = {}
        self._level_counter = 0
        # (session_key, op) for every browse_browse/browse_load — lets tests
        # assert which session an operation actually ran on.
        self.calls: List[tuple[str, str]] = []

    def install_search(self, query: str, roots: List[_Node]) -> None:
        self._assign_keys(roots)
        self._search[query] = roots

    def _assign_keys(self, level: List[_Node]) -> None:
        level_id = self._level_counter
        self._level_counter += 1
        for pos, n in enumerate(level):
            n.item_key = f"{level_id}:{pos}"
            if n.children:
                self._assign_keys(n.children)

    @staticmethod
    def _find(level: List[_Node], item_key: str) -> Optional[_Node]:
        return next((n for n in level if n.item_key == item_key), None)

    def browse_browse(self, opts: dict) -> dict:
        sk = opts.get("multi_session_key", "")
        self.calls.append((sk, "browse"))
        if opts.get("pop_all") and "input" in opts:
            self._stacks[sk] = [list(self._search.get(opts["input"], []))]
        elif "item_key" in opts:
            stack = self._stacks.setdefault(sk, [[]])
            target = self._find(stack[-1], opts["item_key"])
            stack.append(list(target.children) if target else [])
        elif "pop_levels" in opts:
            stack = self._stacks.setdefault(sk, [[]])
            for _ in range(opts["pop_levels"]):
                if len(stack) > 1:
                    stack.pop()
        return {}

    def browse_load(self, opts: dict) -> dict:
        sk = opts.get("multi_session_key", "")
        self.calls.append((sk, "load"))
        stack = self._stacks.get(sk) or [[]]
        level = stack[-1]
        items = [
            {
                "title": n.title,
                "subtitle": n.subtitle,
                "image_key": n.image_key,
                "hint": n.hint,
                "item_key": n.item_key,
            }
            for n in level
        ]
        return {"items": items, "list": {"count": len(items), "title": "", "hint": None}}


class StatefulBrowseFake(RoonBrowseMixin):
    """Roon connection running real browse logic over a stateful per-session
    cursor (see module docstring)."""

    def __init__(self, max_sessions: int = 16) -> None:
        self.session_manager = BrowseSessionManager(max_sessions=max_sessions)
        self.api = _StatefulApi()

    def install_search(self, query: str, roots: List[_Node]) -> None:
        self.api.install_search(query, roots)

    def sessions_used(self) -> List[str]:
        """Distinct session keys any browse op has run on, in first-seen order."""
        seen: List[str] = []
        for sk, _ in self.api.calls:
            if sk not in seen:
                seen.append(sk)
        return seen

    def _lookup_output_id(self, zone: Optional[str] = None) -> str:
        return "fake-output"
