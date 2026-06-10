from __future__ import annotations

from typing import Any, Callable, Optional

from app.constants import CHANNEL_RATE_LIMIT, RATE_LIMIT_MAX_RETRIES


def is_rate_limited_error(err_text: str) -> bool:
    lowered = err_text.lower()
    return any(
        token in lowered
        for token in [
            "rate limit",
            "too many requests",
            "429",
            "tokens per minute",
        ]
    )


def emit_rate_limit_banner(
    ws_send_fn: Optional[Callable[[str, Any], None]],
    agent_name: str,
    *,
    retriable: bool,
    error_text: str,
    retry_in_seconds: int = 0,
    attempt: int = 0,
    max_retries: int = RATE_LIMIT_MAX_RETRIES,
    can_override: bool = False,
    active: bool = True,
) -> None:
    if not ws_send_fn:
        return
    ws_send_fn(CHANNEL_RATE_LIMIT, {
        "active": active,
        "retriable": retriable,
        "agent_name": agent_name,
        "error": error_text,
        "retry_in_seconds": retry_in_seconds,
        "attempt": attempt,
        "max_retries": max_retries,
        "can_override": can_override,
        "display_seconds": 5 if not retriable else 0,
    })
