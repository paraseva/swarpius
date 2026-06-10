"""Redact secrets from text emitted to logs, WS, or error responses.

LiteLLM exception strings can carry partial keys from the provider's
HTTP error responses (URL query params, Authorization / x-api-key headers).
Apply at every chokepoint that emits or logs raw exception text.
"""
from __future__ import annotations

import re
from typing import Optional

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # URL query param: ?key=… or &key=…
    (re.compile(r"([?&]key=)[^\s&'\"]+", re.IGNORECASE), r"\1<redacted>"),
    # Bearer tokens
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-.+/=]{8,}", re.IGNORECASE), r"\1<redacted>"),
    # Authorization / x-api-key / api_key headers (JSON-ish or key=val form)
    (
        re.compile(
            r"(['\"]?(?:x-api-key|Authorization|api[-_]key)['\"]?\s*[:=]\s*['\"]?)"
            r"[A-Za-z0-9_\-.+/=]{8,}",
            re.IGNORECASE,
        ),
        r"\1<redacted>",
    ),
    # OpenAI / Anthropic key prefix
    (re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{20,}"), "<redacted-key>"),
    # Google AI Studio keys
    (re.compile(r"AIza[A-Za-z0-9_\-]{35}"), "<redacted-key>"),
)


def redact_secrets(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text
