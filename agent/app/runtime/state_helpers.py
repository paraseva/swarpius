"""Module-level helpers used by ``RuntimeState`` at initialisation
time. Split out of ``state.py`` to keep the class file focused on
the class itself."""

from __future__ import annotations

import logging
from typing import Optional

from tools.web_search import (
    BraveSearchTool,
    BraveSearchToolConfig,
    SearXNGSearchTool,
    SearXNGSearchToolConfig,
    TavilySearchTool,
    TavilySearchToolConfig,
    WebSearchTool,
)

_log = logging.getLogger("swarpius.runtime")


def _classifier_temperature(
    profile_temperature: Optional[float], *, locked: bool = False,
) -> Optional[float]:
    """Resolve the temperature to use for a classification-type LLM
    client (arbiter, diagnostic agent).

    Pins to 0.0 — these jobs have a target answer, not a creative
    output, so non-determinism is purely harmful. Two carve-outs:

      - ``profile_temperature is None`` — profile says ``temperature:
        null``, model deprecated the param. Keep it None.
      - ``locked=True`` — profile sets ``temperature_lock: true``,
        meaning the model rejects anything other than the profile
        value (e.g. GPT-5 family requires 1.0). drop_params can't
        save us here because the param IS recognised — it's the
        value the model rejects.
    """
    if profile_temperature is None:
        return None
    if locked:
        return profile_temperature
    return 0.0


def _build_web_search_tool(settings) -> Optional[WebSearchTool]:
    """Pick a web-search backend.

    ``WEB_SEARCH_PROVIDER`` must be set explicitly to one of
    ``searxng`` | ``brave`` | ``tavily`` | ``none``. Each provider
    requires its corresponding credential — ``SEARXNG_URL`` for
    searxng, ``BRAVE_API_KEY`` for brave, ``TAVILY_API_KEY`` for
    tavily. If the provider is set but its credential is missing,
    the agent logs a warning and disables web search; the user can
    fix the env and restart.

    Unset (or ``none``) disables web search cleanly — the
    ``web_search`` tool isn't registered, and queries needing
    external knowledge fail closed at the LLM layer.

    Emits exactly one ``Web search backend: …`` line so the chosen
    state is visible at startup.
    """
    explicit = (settings.web_search_provider or "").lower().strip()

    if not explicit or explicit == "none":
        _log.info(
            "Web search backend: disabled (WEB_SEARCH_PROVIDER %s). "
            "Set WEB_SEARCH_PROVIDER to searxng / brave / tavily and "
            "configure the matching credential to enable.",
            "unset" if not explicit else "=none",
        )
        return None

    if explicit == "searxng":
        if not settings.searxng_url:
            _log.warning(
                "Web search backend: disabled — WEB_SEARCH_PROVIDER=searxng "
                "but SEARXNG_URL is not set. Set SEARXNG_URL in agent/.env "
                "(or use `docker compose --profile search` which provides "
                "the URL automatically).",
            )
            return None
        _log.info("Web search backend: searxng (WEB_SEARCH_PROVIDER=searxng)")
        return SearXNGSearchTool(SearXNGSearchToolConfig(
            base_url=settings.searxng_url, max_results=5,
        ))

    if explicit == "brave":
        if not settings.brave_api_key:
            _log.warning(
                "Web search backend: disabled — WEB_SEARCH_PROVIDER=brave "
                "but BRAVE_API_KEY is not set.",
            )
            return None
        _log.info("Web search backend: brave (WEB_SEARCH_PROVIDER=brave)")
        return BraveSearchTool(BraveSearchToolConfig(
            api_key=settings.brave_api_key, max_results=5,
        ))

    if explicit == "tavily":
        if not settings.tavily_api_key:
            _log.warning(
                "Web search backend: disabled — WEB_SEARCH_PROVIDER=tavily "
                "but TAVILY_API_KEY is not set.",
            )
            return None
        _log.info("Web search backend: tavily (WEB_SEARCH_PROVIDER=tavily)")
        return TavilySearchTool(TavilySearchToolConfig(
            api_key=settings.tavily_api_key, max_results=5,
        ))

    _log.warning(
        "Web search backend: disabled — unknown WEB_SEARCH_PROVIDER %r. "
        "Valid values: searxng, brave, tavily, none.",
        explicit,
    )
    return None


def _backend_ok(backends: list, backend_id: str) -> bool:
    """Return True iff a backend with the given id is present in the
    validator's latest probe results and reported ``ok=True``."""
    for b in backends:
        if getattr(b, "backend", None) == backend_id:
            return bool(getattr(b, "ok", False))
    return False
