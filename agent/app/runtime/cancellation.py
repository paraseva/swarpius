from __future__ import annotations

from typing import Any

from app.exceptions import RequestInterrupted

# "stop" is intentionally not a directive — bare "stop" overloads with
# the transport command "stop playing", so it flows through the LLM
# which has the context to disambiguate.
_CANCEL_EXACT = {"cancel", "abort", "nevermind", "never mind", "quit"}
_CANCEL_PREFIX = {"cancel", "abort", "quit"}


def is_explicit_interrupt_message(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if lowered in _CANCEL_EXACT:
        return True
    return any(lowered.startswith(word + " ") for word in _CANCEL_PREFIX)


def raise_if_cancelled(cancel_event: Any, context: str) -> None:
    if cancel_event and cancel_event.is_set():
        raise RequestInterrupted(f"Cancelled while {context}.")
