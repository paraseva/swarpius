"""Provider-specific API key / connectivity validation.

Dispatches to per-provider checkers that hit each provider's
cheapest auth endpoint. Providers with free metadata endpoints
(OpenAI / Anthropic /v1/models, etc.) get real validation; those
without (Brave, Tavily) get ``not_validated`` rather than a billable
query.

10-second timeout per check; network / timeout errors surface as
``ok=False`` with ``error_kind=network``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import requests

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_USER_AGENT = "Swarpius-Settings-Test/1.0"


def handle_test(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch to the per-provider checker.

    Request shape varies by provider; ``provider`` is always present.
    LLM providers take ``api_key``; Ollama / SearXNG take ``url``.
    Brave / Tavily save without validation (no free auth check).

    Response shape::

        {"ok": True,  "provider": "...", "detail": "..."}
        {"ok": False, "provider": "...", "error_kind": "...", "detail": "..."}
        {"ok": True,  "provider": "...", "not_validated": True,
         "detail": "..."}

    ``error_kind`` is one of: ``auth_failed`` (401/403), ``not_found``
    (404), ``network`` (DNS/connect/timeout), ``other`` (5xx/unknown).
    """
    provider = (payload.get("provider") or "").strip().lower()
    if not provider:
        return {"ok": False, "error_kind": "other", "detail": "missing provider"}

    model = (payload.get("model") or "").strip()
    if model and provider in _LLM_PROVIDERS_WITH_TUPLE_CHECK:
        return _check_llm_tuple(provider, model, payload)

    checker = _CHECKERS.get(provider)
    if checker is None:
        # Unknown provider — no free auth check, but it may still be
        # a valid LiteLLM-supported one. If an api_key is supplied,
        # save it without validation rather than failing the test.
        api_key = (payload.get("api_key") or "").strip()
        if not api_key:
            return _error(
                provider,
                "auth_failed",
                f"No checker for {provider!r} and no API key provided",
            )
        return {
            "ok": True,
            "provider": provider,
            "not_validated": True,
            "detail": (
                f"We don't have a free auth check for '{provider}'. "
                f"Key saved; the first chat request will tell you if "
                f"it works."
            ),
        }

    try:
        return checker(payload)
    except requests.exceptions.Timeout:
        return _error(provider, "network", f"Connection to {provider} timed out")
    except requests.exceptions.ConnectionError as exc:
        return _error(provider, "network", f"Network error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected error testing %s", provider)
        return _error(provider, "other", f"Unexpected error: {exc}")


def persist_backend_test_result(
    payload: Dict[str, Any], result: Dict[str, Any],
) -> None:
    """Persist a Settings-Test result for a non-LLM backend into the live
    validation status, so the Settings highlight clears/sets and survives
    a browser refresh.

    Only persisted when the tested config matches what's saved
    (``matches_saved``); a test of unsaved edits is left ephemeral, since
    the UI already prompts to save & validate for changed values."""
    if not payload.get("matches_saved"):
        return
    from app.settings.backends import backend_for_provider
    mapping = backend_for_provider(payload.get("provider") or "")
    if mapping is None:
        return
    backend_id, label = mapping
    from app.settings.validation import BackendResult, get_validator
    get_validator().update_backend(BackendResult(
        backend=backend_id,
        label=label,
        ok=bool(result.get("ok")),
        error_kind=result.get("error_kind"),
        detail=result.get("detail"),
    ))


def handle_test_and_persist(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Settings Test and, for a saved-config backend, persist the
    outcome to the validation status. The WS layer uses this wrapper so a
    Test result reflects in the Settings highlight and survives a refresh."""
    result = handle_test(payload)
    persist_backend_test_result(payload, result)
    return result


def _check_anthropic(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Anthropic — GET /v1/models. Free, just returns the model list."""
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        return _error("anthropic", "auth_failed", "API key is empty")
    r = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": _USER_AGENT,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    return _interpret_auth_response("anthropic", r, "Anthropic API key valid")


def _check_openai(payload: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI — GET /v1/models. Free."""
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        return _error("openai", "auth_failed", "API key is empty")
    r = requests.get(
        "https://api.openai.com/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    return _interpret_auth_response("openai", r, "OpenAI API key valid")


def _check_gemini(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Google Gemini — GET /v1beta/models?key=<api_key>. Free.

    Provider id is ``gemini`` to match LiteLLM's canonical naming
    (``gemini/<model>``) and the ``LLM_API_KEY_GEMINI`` env var.
    """
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        return _error("gemini", "auth_failed", "API key is empty")
    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT_SECONDS,
    )
    return _interpret_auth_response("gemini", r, "Gemini API key valid")


def _check_ollama(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ollama — GET <base>/api/tags. Connectivity check, no key needed."""
    base = (payload.get("url") or "http://localhost:11434").strip().rstrip("/")
    r = requests.get(
        f"{base}/api/tags",
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT_SECONDS,
    )
    if r.status_code == 200:
        try:
            models = r.json().get("models") or []
            return _ok(
                "ollama",
                f"Connected to Ollama at {base} ({len(models)} model(s) available)",
            )
        except ValueError:
            return _ok("ollama", f"Connected to Ollama at {base}")
    return _error("ollama", "other", f"Unexpected HTTP {r.status_code} from {base}")


def _check_searxng(payload: Dict[str, Any]) -> Dict[str, Any]:
    """SearXNG — GET <url>/. Just confirms the server is reachable."""
    url = (payload.get("url") or "").strip().rstrip("/")
    if not url:
        return _error("searxng", "other", "URL is empty")
    r = requests.get(
        f"{url}/",
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT_SECONDS,
        allow_redirects=True,
    )
    if r.status_code == 200:
        return _ok("searxng", f"Connected to SearXNG at {url}")
    return _error("searxng", "other", f"Unexpected HTTP {r.status_code} from {url}")


def _check_brave(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Brave — no free auth-only endpoint; we'd need to spend a query
    to validate. Skip with not_validated for now; the key still saves."""
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        return _error("brave", "auth_failed", "API key is empty")
    return {
        "ok": True,
        "provider": "brave",
        "not_validated": True,
        "detail": (
            "Brave doesn't expose a free auth check — the key is saved "
            "but not verified. The next web search will use 1 query "
            "from your monthly quota; check the logs if it fails."
        ),
    }


def _check_tavily(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Tavily — same story as Brave."""
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        return _error("tavily", "auth_failed", "API key is empty")
    return {
        "ok": True,
        "provider": "tavily",
        "not_validated": True,
        "detail": (
            "Tavily doesn't expose a free auth check — the key is saved "
            "but not verified. The next web search will use 1 search "
            "credit; check the logs if it fails."
        ),
    }


# ── Per-row LLM tuple check ───────────────────────────────────────


_LLM_PROVIDERS_WITH_TUPLE_CHECK = frozenset(
    {"anthropic", "openai", "gemini", "ollama", "ollama_chat"},
)


def _check_llm_tuple(
    provider: str, model: str, payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate a (provider, model, api_key) row against the live provider."""
    from app.settings.validation import _check_tuple as _validate_tuple

    api_key = (payload.get("api_key") or "").strip()
    result = _validate_tuple((provider, model, api_key))
    result.setdefault("provider", provider)
    return result


# ── Helpers ────────────────────────────────────────────────────────


def _ok(provider: str, detail: str) -> Dict[str, Any]:
    return {"ok": True, "provider": provider, "detail": detail}


def _error(provider: str, kind: str, detail: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "provider": provider,
        "error_kind": kind,
        "detail": detail,
    }


def _interpret_auth_response(
    provider: str, response: requests.Response, success_detail: str,
) -> Dict[str, Any]:
    """Map HTTP status codes to our uniform response shape.

    Used for the metadata-endpoint checks (Anthropic, OpenAI, Google),
    which all return 200 on success, 401/403 on bad credentials, and
    error codes for other failures.
    """
    if response.status_code == 200:
        return _ok(provider, success_detail)
    if response.status_code in (401, 403):
        return _error(provider, "auth_failed", f"HTTP {response.status_code}: invalid API key")
    if response.status_code == 404:
        return _error(provider, "not_found", f"HTTP 404 from {provider}")
    return _error(provider, "other", f"Unexpected HTTP {response.status_code}")


def _check_tts(payload: Dict[str, Any]) -> Dict[str, Any]:
    """F5-TTS — TCP connect probe of the configured ``host:port``.
    Anything that accepts a TCP connect on the port counts as
    reachable; the full TTS handshake only runs once a real
    synthesis request is in flight."""
    import socket

    from tts.url import TtsUrlError, parse_tts_url

    raw = (payload.get("url") or "").strip()
    try:
        host, port = parse_tts_url(raw)
    except TtsUrlError as exc:
        return _error("tts", "other", str(exc))
    label = f"{host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=_TIMEOUT_SECONDS):
            pass
    except socket.timeout:
        return _error("tts", "network", f"Connection to {label} timed out")
    except OSError as exc:
        return _error("tts", "network", f"Connection refused: {exc}")
    return _ok("tts", f"Reachable at {label}")


_CHECKERS = {
    "anthropic": _check_anthropic,
    "openai": _check_openai,
    "gemini": _check_gemini,
    "ollama": _check_ollama,
    "ollama_chat": _check_ollama,
    "searxng": _check_searxng,
    "brave": _check_brave,
    "tavily": _check_tavily,
    "tts": _check_tts,
}
