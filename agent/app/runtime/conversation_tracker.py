"""Conversation tracking for request log grouping.

Manages topic-based conversation threads. Each thread has an ID (cXX format),
a topic summary, and timestamp tracking. Initially uses timeout-based assignment;
the diagnostic agent provides smarter LLM-driven classification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def day_str(timestamp: float) -> str:
    """Local calendar day of a wall-clock timestamp, matching the date used for
    conversation log directories (``request_logger`` uses ``datetime.now()``)."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


@dataclass
class ConversationThread:
    """A single conversation thread with topic tracking."""

    id: str  # "c01"
    numeric_id: int  # 1
    topic_summary: str = ""
    last_request_timestamp: float = 0.0
    request_count: int = 0
    last_response_summary: str = ""


class ConversationTracker:
    """Tracks active conversation threads and assigns requests to them.

    Defaults to idle-timeout-based assignment. The diagnostic agent
    can update assignments with LLM-driven classification via
    update_topic() and reassign_current().
    """

    def __init__(
        self,
        idle_timeout_seconds: int = 300,
        start_conversation_num: int = 1,
        max_conversations: int = 20,
        aging_hours: float = 24.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._idle_timeout = idle_timeout_seconds
        self._max_conversations = max_conversations
        self._aging_seconds = aging_hours * 3600
        self._clock = clock
        self._threads: Dict[str, ConversationThread] = {}
        self._current_id: Optional[str] = None
        self._next_num: int = start_conversation_num
        self._last_request_time: float = 0.0

    def assign_by_timeout(self) -> str:
        """Assign a conversation using idle-timeout logic.

        Returns the conversation ID (e.g. "c01"). Creates a new conversation
        if this is the first request or if the idle timeout has been exceeded.
        """
        now = self._clock()
        if self._current_id is None:
            return self._mint_new(now)

        if self._last_request_time and (now - self._last_request_time) >= self._idle_timeout:
            return self._mint_new(now)

        thread = self._threads[self._current_id]
        thread.last_request_timestamp = now
        thread.request_count += 1
        self._last_request_time = now
        return self._current_id

    def new_conversation(self) -> str:
        """Explicitly start a new conversation (e.g. on WS reconnect)."""
        return self._mint_new(self._clock())

    def reset(self) -> None:
        """Drop all conversation state. Used at the calendar-day boundary so a
        new day starts fresh grouping (c01) rather than continuing the previous
        day's thread."""
        self._threads = {}
        self._current_id = None
        self._next_num = 1
        self._last_request_time = 0.0

    @property
    def current_day(self) -> Optional[str]:
        """Local calendar day of the last assigned request, or None if none has
        been assigned. Derived from ``last_request_time`` so it survives a
        restart with the persisted state — no separate persisted field."""
        if not self._last_request_time:
            return None
        return day_str(self._last_request_time)

    def update_topic(self, conversation_id: str, topic_summary: str) -> None:
        """Update a conversation's topic summary."""
        thread = self._threads.get(conversation_id)
        if thread:
            thread.topic_summary = topic_summary

    def reassign_current(self, conversation_id: str, topic_summary: str) -> None:
        """Reassign the current conversation to a different thread.

        Used by the diagnostic agent when it determines the current request
        belongs to a different conversation than what timeout-based logic assigned.
        """
        if conversation_id in self._threads:
            now = self._clock()
            thread = self._threads[conversation_id]
            thread.topic_summary = topic_summary
            thread.last_request_timestamp = now
            self._current_id = conversation_id
            self._last_request_time = now

    def set_last_response(self, conversation_id: str, summary: str) -> None:
        """Store a truncated summary of the last agent response for a conversation."""
        thread = self._threads.get(conversation_id)
        if thread:
            thread.last_response_summary = summary

    @property
    def now(self) -> float:
        """Current time from the tracker's clock."""
        return self._clock()

    def get_active_threads(self) -> List[ConversationThread]:
        """Return active (non-aged) conversations, most recent first.

        Used by the diagnostic agent to build its classification prompt.
        """
        now = self._clock()
        active = [
            t for t in self._threads.values()
            if (now - t.last_request_timestamp) < self._aging_seconds
        ]
        active.sort(key=lambda t: t.last_request_timestamp, reverse=True)
        return active[:self._max_conversations]

    @property
    def current_id(self) -> str:
        """Current conversation ID in cXX format."""
        return self._current_id or f"c{self._next_num:02d}"

    @property
    def current_numeric(self) -> int:
        """Current conversation number."""
        if self._current_id and self._current_id in self._threads:
            return self._threads[self._current_id].numeric_id
        return self._next_num

    # ── Persistence ────────────────────────────────────────────────

    def capture_state(self) -> Dict[str, Any]:
        """Snapshot the conversation threads + counters. Timestamps are
        wall-clock, so they stay comparable after a restart."""
        return {
            "threads": {
                conv_id: {
                    "id": t.id,
                    "numeric_id": t.numeric_id,
                    "topic_summary": t.topic_summary,
                    "last_request_timestamp": t.last_request_timestamp,
                    "request_count": t.request_count,
                    "last_response_summary": t.last_response_summary,
                }
                for conv_id, t in self._threads.items()
            },
            "current_id": self._current_id,
            "next_num": self._next_num,
            "last_request_time": self._last_request_time,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self._threads = {
            conv_id: ConversationThread(
                id=t["id"],
                numeric_id=t["numeric_id"],
                topic_summary=t.get("topic_summary", ""),
                last_request_timestamp=t.get("last_request_timestamp", 0.0),
                request_count=t.get("request_count", 0),
                last_response_summary=t.get("last_response_summary", ""),
            )
            for conv_id, t in data.get("threads", {}).items()
        }
        self._current_id = data.get("current_id")
        self._next_num = data.get("next_num", 1)
        self._last_request_time = data.get("last_request_time", 0.0)

    def _mint_new(self, now: float) -> str:
        """Create a new conversation thread and set it as current."""
        num = self._next_num
        conv_id = f"c{num:02d}"
        self._threads[conv_id] = ConversationThread(
            id=conv_id,
            numeric_id=num,
            last_request_timestamp=now,
            request_count=1,
        )
        self._current_id = conv_id
        self._next_num = num + 1
        self._last_request_time = now
        return conv_id
