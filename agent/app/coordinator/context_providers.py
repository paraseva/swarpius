from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Callable, Deque, Optional

from app.time_utils import format_relative_time


class ContextProvider:
    def __init__(self, title: str) -> None:
        self.title = title

    def get_info(self) -> str:
        return ""


class CurrentDateProvider(ContextProvider):
    def __init__(self, title: str) -> None:
        super().__init__(title)

    def get_info(self) -> str:
        return f"Current date: {datetime.now().strftime('%A, %Y-%m-%d')}"


class CurrentTimeProvider(ContextProvider):
    def __init__(self, title: str) -> None:
        super().__init__(title)

    def get_info(self) -> str:
        return f"Current time (HH:MM:SS): {datetime.now().strftime('%H:%M:%S')}"


class TextContextProvider(ContextProvider):
    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.value = ""

    def set_context(self, value: str) -> None:
        self.value = value

    def get_info(self) -> str:
        return self.value


class CallbackContextProvider(ContextProvider):
    """Provider that calls a function to get its content each time."""

    def __init__(self, title: str, callback: Callable[[], str]) -> None:
        super().__init__(title)
        self._callback = callback

    def get_info(self) -> str:
        return self._callback()


class ConversationHistoryProvider(ContextProvider):
    """Stores past user/agent turns and renders them as a timestamped
    transcript. Each turn carries an absolute ``datetime`` (assigned at
    ``add_turn`` time) so the rendered output can show *when* each
    exchange happened — both as a relative phrase ("12 hr ago") and an
    absolute timestamp. Surfacing staleness is load-bearing: zone /
    default-zone claims in old turns must not be mistaken for live
    state."""

    def __init__(self, title: str, max_turns: int = 5) -> None:
        super().__init__(title)
        self.history: Deque[dict[str, object]] = deque(maxlen=max_turns)

    def add_turn(
        self,
        user_input: str,
        agent_response: str,
        *,
        timestamp: Optional[datetime] = None,
    ) -> None:
        self.history.append({
            "user": user_input,
            "agent": agent_response,
            "timestamp": timestamp or datetime.now(),
        })

    def get_info(self) -> str:
        if not self.history:
            return ""
        now = datetime.now()
        blocks = []
        for turn in self.history:
            ts = turn["timestamp"]
            relative = format_relative_time((now - ts).total_seconds())
            absolute = ts.strftime("%Y-%m-%d %H:%M")
            blocks.append(
                f"[{relative} — {absolute}]\n"
                f"User: {turn['user']}\n"
                f"Swarpius: {turn['agent']}"
            )
        return "\n\n".join(blocks)
