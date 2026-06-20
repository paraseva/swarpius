"""Persistence participant for the coordinator's working memory.

Captures and restores manifest group A — the cross-turn memory that lets the
model continue a conversation after a restart — as one coupled unit:
conversation turns (with their original timestamps), the execution trace and
step counter, and the result store + search history (the handles the model
fetches). These reference each other, so they are saved/restored together.

Whether this is one participant or several is an internal detail; callers go
through ``RuntimeState.attach_persistence``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from app.runtime.state_internals import SearchHistoryEntry


class WorkingMemoryState:
    """A :class:`~app.runtime.persistence.PersistentState` over a runtime's
    working memory."""

    state_key = "working_memory"

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime

    def capture_state(self) -> Dict[str, Any]:
        rt = self._rt
        turns = [
            {
                "user": turn["user"],
                "agent": turn["agent"],
                "timestamp": turn["timestamp"].isoformat(),
            }
            for turn in rt.conversation_history_provider.history
        ]
        search_history = [
            {
                "result_handle": entry.result_handle,
                "tool_name": entry.tool_name,
                "description": entry.description,
                "item_count": entry.item_count,
                "timestamp_ms": entry.timestamp_ms,
                "timestamp_display": entry.timestamp_display,
                "session_key": entry.session_key,
            }
            for entry in rt.results.history
        ]
        return {
            "conversation_turns": turns,
            "execution_trace": rt.execution_trace,
            "global_step": rt.global_step,
            "result_entries": rt.results.entries,
            "search_history": search_history,
            "result_counter": rt.results.counter,
            "last_result_handle": rt.results.last_handle,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        rt = self._rt

        # Conversation turns — re-inject with their ORIGINAL timestamps so the
        # provider's staleness rendering stays truthful (a restored turn must
        # not read as if it just happened).
        rt.conversation_history_provider.history.clear()
        for turn in data.get("conversation_turns", []):
            rt.conversation_history_provider.add_turn(
                turn["user"],
                turn["agent"],
                timestamp=datetime.fromisoformat(turn["timestamp"]),
            )

        rt.execution_trace[:] = data.get("execution_trace", [])
        rt.global_step = data.get("global_step", 0)

        # result_store / search_history are by-reference views into the
        # manager's collections (tools captured them at registration), so
        # mutate in place — never reassign.
        rt.results.entries.clear()
        rt.results.entries.update(data.get("result_entries", {}))
        rt.results.history[:] = [
            SearchHistoryEntry(**entry) for entry in data.get("search_history", [])
        ]
        rt.results.counter = data.get("result_counter", 0)
        rt.results.last_handle = data.get("last_result_handle")
