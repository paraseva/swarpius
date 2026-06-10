"""Backend reachability registry.

A single source of truth for the non-LLM backends the agent can use (web
search, TTS): how to probe each for status, and whether it's safe to poll
on a timer. Boot/save validation, the background health loop, and the
Settings Test button all consume this, so selection and labelling live in
one place. The probes themselves stay in :mod:`app.settings.validation`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from app.settings import Settings
from app.settings.validation import (
    BackendResult,
    _check_brave_configured,
    _check_tavily_configured,
    _probe_searxng,
    probe_tts,
)


@dataclass(frozen=True)
class BackendCheck:
    """One configured backend's status check.

    ``pollable`` marks backends that are free and meaningful to probe on a
    timer (SearXNG, TTS). Brave/Tavily are not pollable — a real probe
    would spend a query, so they're only key-presence checked at boot/save.
    """
    backend_id: str   # matches BackendResult.backend ("web-search" | "tts")
    label: str
    status_probe: Callable[[Settings], BackendResult]
    pollable: bool


# Settings-Test provider id -> (backend_id, label). SearXNG / Brave /
# Tavily all resolve to the single "web-search" backend row.
_PROVIDER_BACKENDS: Dict[str, Tuple[str, str]] = {
    "searxng": ("web-search", "SearXNG"),
    "brave": ("web-search", "Brave Search"),
    "tavily": ("web-search", "Tavily"),
    "tts": ("tts", "F5-TTS server"),
}


def backend_for_provider(provider: str) -> Optional[Tuple[str, str]]:
    """``(backend_id, label)`` for a Settings-Test provider, or ``None``
    when the provider isn't a non-LLM backend (e.g. an LLM provider)."""
    return _PROVIDER_BACKENDS.get((provider or "").strip().lower())


def active_backend_checks(settings: Settings) -> List[BackendCheck]:
    """The backend checks implied by current config — web search resolves
    to the selected provider; TTS is included when a URL is configured."""
    checks: List[BackendCheck] = []
    provider = (settings.web_search_provider or "").strip().lower()
    if provider == "searxng":
        checks.append(BackendCheck(
            "web-search", "SearXNG", _probe_searxng, pollable=True))
    elif provider == "brave":
        checks.append(BackendCheck(
            "web-search", "Brave Search", _check_brave_configured,
            pollable=False))
    elif provider == "tavily":
        checks.append(BackendCheck(
            "web-search", "Tavily", _check_tavily_configured, pollable=False))
    if settings.tts_url:
        checks.append(BackendCheck(
            "tts", "F5-TTS server", probe_tts, pollable=True))
    return checks
