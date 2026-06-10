"""Centralised LLM-error taxonomy — the single classifier for every agent.

Each agent maps a kind to its own action (the coordinator flags the
validator + re-raises so the chat surfaces it; the analyser retries
transient errors and halts on permanent ones), but the *classification*
lives here only — no per-agent re-implementations.

Prefers ``isinstance`` against litellm's exception types (robust); falls
back to class-name / message matching so it still classifies when those
types aren't importable (e.g. a decoupled caller, or a provider whose
errors aren't litellm subclasses).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

ErrorKind = Literal[
    "auth",            # bad / expired key, permission denied
    "not_found",       # unknown model or endpoint
    "bad_request",     # invalid or deprecated param, malformed request
    "context_length",  # input exceeds the model's context window
    "rate_limited",    # 429 throttling
    "transient",       # connection blip, 5xx, timeout, or unknown
]

# Kinds that won't succeed on a retry — a config / auth / request problem
# the user needs to fix. The others are worth retrying with backoff.
_PERMANENT = frozenset({"auth", "not_found", "bad_request"})


def is_permanent(kind: ErrorKind) -> bool:
    """True for errors a retry can't fix (auth, not-found, bad-request)."""
    return kind in _PERMANENT


def classify_llm_error(
    exc: BaseException, *, litellm_module: Optional[Any] = None,
) -> ErrorKind:
    """Classify an LLM-provider exception into the shared taxonomy."""
    lm = litellm_module
    if lm is None:
        try:
            import litellm as lm  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 — fall back to name/message matching
            lm = None

    if lm is not None:
        def _is(attr: str) -> bool:
            cls = getattr(lm, attr, None)
            return bool(cls) and isinstance(exc, cls)

        if _is("AuthenticationError") or _is("PermissionDeniedError"):
            return "auth"
        if _is("NotFoundError"):
            return "not_found"
        if _is("ContextWindowExceededError"):
            return "context_length"
        if _is("RateLimitError"):
            return "rate_limited"
        if _is("BadRequestError"):
            return "bad_request"

    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if ("context" in name and ("length" in name or "window" in name)) \
            or "maximum context" in msg or "context length" in msg or "too large" in msg:
        return "context_length"
    if "auth" in name or "permission" in name or "forbidden" in name \
            or "unauthorized" in msg or "401" in msg or "403" in msg \
            or ("invalid" in msg and ("api" in msg or "key" in msg or "credential" in msg)):
        return "auth"
    if "notfound" in name or "not_found" in msg or "404" in msg:
        return "not_found"
    if "ratelimit" in name or "rate limit" in msg or "429" in msg:
        return "rate_limited"
    if "badrequest" in name or "invalid_request" in msg or "400" in msg:
        return "bad_request"
    return "transient"
