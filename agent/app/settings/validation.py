"""Server-side validation of the agent's LLM configuration.

For every enabled agent, checks that its (provider, model, api_key)
tuple resolves against the live provider — API key valid, model in
the catalogue, host reachable. Tuples are deduplicated; HTTP calls
fan out via ``asyncio.gather`` so boot latency is bounded by the
slowest single provider, not the sum.

State is in-memory only — re-validates on boot, on Save & Validate,
and on demand.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app.data_paths import _running_in_docker
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_USER_AGENT = "Swarpius-Validator/1.0"


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(min_value, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, min_value: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(min_value, float(raw))
    except ValueError:
        return default


# Backend reachability probes (SearXNG, F5-TTS). The retry exists for the
# Docker compose co-start race (the agent boots before a sibling service
# is listening), so it's gated to Docker in `_retry_transient` — native/
# bundle does a single attempt. Only transient network failures retry;
# config-shape ones (missing key, malformed URL, HTTP error) don't. The
# probe timeout is tighter than the LLM checks': a reachable local/LAN
# service answers in milliseconds, so a short one bounds how long a down
# backend stalls boot (and keeps the Ctrl-C window small).
_BACKEND_PROBE_ATTEMPTS = _env_int("BACKEND_PROBE_ATTEMPTS", 5)
_BACKEND_PROBE_BACKOFF_SECONDS = _env_float("BACKEND_PROBE_BACKOFF_SECONDS", 4.0)
_BACKEND_PROBE_TIMEOUT_SECONDS = _env_float("BACKEND_PROBE_TIMEOUT_SECONDS", 5.0)

_LOCAL_PROVIDERS = frozenset({"ollama", "ollama_chat"})

AGENTS: Tuple[str, ...] = ("coordinator", "arbiter", "diagnostic", "analyser")


class ValidationState(str, Enum):
    OPEN = "open"
    VALIDATING = "validating"
    FAILED = "failed"
    PASSED = "passed"


@dataclass
class AgentResult:
    """Validation outcome for a single agent row."""
    agent: str
    enabled: bool
    provider: Optional[str]
    model: Optional[str]
    inherits_coordinator: bool
    ok: Optional[bool] = None
    error_kind: Optional[str] = None
    detail: Optional[str] = None
    not_validated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "inherits_coordinator": self.inherits_coordinator,
            "ok": self.ok,
            "error_kind": self.error_kind,
            "detail": self.detail,
            "not_validated": self.not_validated,
        }


@dataclass
class BackendResult:
    """Reachability check for a non-LLM backend (web search / TTS).

    These don't have a state machine like the LLM agents — they're a
    flat list of "is this reachable / configured?" results that
    surface as tab badges and the Settings nav-icon marker.
    """
    backend: str
    label: str
    ok: bool
    error_kind: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "label": self.label,
            "ok": self.ok,
            "error_kind": self.error_kind,
            "detail": self.detail,
        }


@dataclass
class ValidationStatus:
    state: ValidationState
    results: List[AgentResult] = field(default_factory=list)
    backends: List[BackendResult] = field(default_factory=list)
    pending_restart: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "results": [r.to_dict() for r in self.results],
            "backends": [b.to_dict() for b in self.backends],
            "pending_restart": self.pending_restart,
        }


TupleKey = Tuple[str, str, str]


def _split_model(spec: Optional[str]) -> Tuple[str, str]:
    """``anthropic/claude-sonnet-4-6`` → (``anthropic``, ``claude-sonnet-4-6``).
    Returns ``("", "")`` for any malformed spec."""
    if not spec or "/" not in spec:
        return "", ""
    provider, _, model = spec.partition("/")
    return provider.strip().lower(), model.strip()


def _has_explicit_override(settings: Settings, agent: str) -> bool:
    if agent == "arbiter":
        return bool(settings.llm_model_arbiter)
    if agent == "diagnostic":
        return bool(settings.llm_model_diagnostic)
    if agent == "analyser":
        return bool(settings.llm_model_analyser)
    return False


class ConfigValidator:
    """In-memory validation state. ``validate()`` runs the full check
    and emits transitions via the broadcast callback; sync callers can
    flag a runtime failure via ``mark_provider_failed``.
    """

    def __init__(
        self,
        broadcast: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        self._status = ValidationStatus(state=ValidationState.OPEN)
        self._broadcast = broadcast
        # ``_lock`` serialises async validate() calls; ``_state_lock``
        # guards _status against the cross-thread runtime-failure
        # hook racing with it.
        self._lock = asyncio.Lock()
        self._state_lock = threading.Lock()

    def current(self) -> ValidationStatus:
        return self._status

    def set_pending_restart(self, pending: bool) -> None:
        with self._state_lock:
            self._status.pending_restart = pending
        self._emit()

    def reset(self) -> None:
        with self._state_lock:
            self._status = ValidationStatus(state=ValidationState.OPEN)
        self._emit()

    def update_backend(self, result: "BackendResult") -> bool:
        """Replace (or add) the backend with matching ``backend`` id.
        Emits — and returns True — only when ``ok`` transitions, so a
        steady-state poller doesn't spam the channel."""
        changed = False
        with self._state_lock:
            new_backends: List[BackendResult] = []
            found = False
            for existing in self._status.backends:
                if existing.backend == result.backend:
                    found = True
                    if existing.ok != result.ok:
                        changed = True
                    new_backends.append(result)
                else:
                    new_backends.append(existing)
            if not found:
                new_backends.append(result)
                changed = True
            self._status = ValidationStatus(
                state=self._status.state,
                results=self._status.results,
                backends=new_backends,
                pending_restart=self._status.pending_restart,
            )
        if changed:
            self._emit()
        return changed

    def mark_provider_failed(
        self, provider: str, error_kind: str, detail: str,
    ) -> None:
        """Flag every enabled agent row whose provider matches as
        failed at runtime. Called when a live LLM call hits an
        authentication or model-not-found error so the UI surfaces
        the problem without waiting for the next Save & Validate.

        Safe to call from sync executor threads — protected by the
        state lock and emits via the broadcast callback.
        """
        provider_lower = provider.lower()
        with self._state_lock:
            new_results: List[AgentResult] = []
            changed = False
            for r in self._status.results:
                if (
                    r.enabled
                    and r.provider == provider_lower
                    and r.ok is not False
                ):
                    new_results.append(replace(
                        r,
                        ok=False,
                        error_kind=error_kind,
                        detail=detail,
                        not_validated=False,
                    ))
                    changed = True
                else:
                    new_results.append(r)
            if not changed:
                return
            self._status = ValidationStatus(
                state=ValidationState.FAILED,
                results=new_results,
                backends=self._status.backends,
                pending_restart=False,
            )
        self._emit()

    def _emit(self) -> None:
        if self._broadcast is None:
            return
        try:
            self._broadcast(self._status.to_dict())
        except Exception:
            log.exception("validation-status broadcast failed")

    async def validate(
        self, settings: Optional[Settings] = None,
    ) -> ValidationStatus:
        async with self._lock:
            if settings is None:
                settings = get_settings()
            specs = self._build_specs(settings)
            tuples = self._collect_unique_tuples(specs)

            with self._state_lock:
                self._status = ValidationStatus(
                    state=ValidationState.VALIDATING,
                    results=[],
                    backends=self._status.backends,
                    pending_restart=self._status.pending_restart,
                )
            self._emit()

            # Fan out the LLM checks and backend probes together so total
            # latency is the slowest single check, not the sum. The active
            # backends come from the shared registry (app.settings.backends),
            # imported lazily to avoid an import cycle.
            from app.settings.backends import active_backend_checks
            checks = active_backend_checks(settings)
            gathered = await asyncio.gather(
                *(asyncio.to_thread(_check_tuple, t) for t in tuples),
                *(asyncio.to_thread(c.status_probe, settings) for c in checks),
            )
            tuple_results = dict(zip(tuples, gathered[: len(tuples)]))
            backends = list(gathered[len(tuples):])

            agent_results = [
                self._build_agent_result(spec, tuple_results)
                for spec in specs
            ]
            # Only the coordinator gates startup. Sub-agents (arbiter,
            # diagnostic, analyser) and backends (search, TTS) degrade
            # gracefully — their failures are reported, not fatal.
            coordinator_failed = any(
                r.agent == "coordinator" and r.ok is False
                for r in agent_results
            )
            state = (
                ValidationState.FAILED if coordinator_failed
                else ValidationState.PASSED
            )
            with self._state_lock:
                self._status = ValidationStatus(
                    state=state,
                    results=agent_results,
                    backends=backends,
                    pending_restart=self._status.pending_restart,
                )
            self._emit()
            return self._status

    def _build_specs(self, settings: Settings) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for agent in AGENTS:
            enabled = settings.agent_enabled(agent)
            model_spec = settings.model_for(agent)
            provider, model = _split_model(model_spec)
            inherits = (
                agent != "coordinator"
                and not _has_explicit_override(settings, agent)
            )
            api_key = (
                settings.api_key_for_provider(provider) or ""
                if provider else ""
            )
            specs.append({
                "agent": agent,
                "enabled": enabled,
                "provider": provider or None,
                "model": model or None,
                "model_spec": model_spec,
                "inherits_coordinator": inherits,
                "api_key": api_key,
            })
        return specs

    def _collect_unique_tuples(
        self, specs: List[Dict[str, Any]],
    ) -> List[TupleKey]:
        seen: Dict[TupleKey, None] = {}
        for s in specs:
            if not s["enabled"] or not s["provider"] or not s["model"]:
                continue
            key: TupleKey = (s["provider"], s["model"], s["api_key"])
            seen.setdefault(key, None)
        return list(seen.keys())

    def _build_agent_result(
        self,
        spec: Dict[str, Any],
        tuple_results: Dict[TupleKey, Dict[str, Any]],
    ) -> AgentResult:
        result = AgentResult(
            agent=spec["agent"],
            enabled=spec["enabled"],
            provider=spec["provider"],
            model=spec["model_spec"],
            inherits_coordinator=spec["inherits_coordinator"],
        )
        if not spec["enabled"]:
            result.detail = "Disabled"
            return result
        if not spec["provider"] or not spec["model"]:
            result.ok = False
            result.error_kind = "other"
            result.detail = (
                "Model spec missing or malformed (expected provider/model)"
            )
            return result
        key: TupleKey = (spec["provider"], spec["model"], spec["api_key"])
        check = tuple_results.get(key, {})
        result.ok = bool(check.get("ok"))
        result.error_kind = check.get("error_kind")
        result.detail = check.get("detail")
        result.not_validated = bool(check.get("not_validated", False))
        return result


# ── Per-provider checkers ──────────────────────────────────────────


def _check_tuple(key: TupleKey) -> Dict[str, Any]:
    provider, model, api_key = key
    checker = _CHECKERS.get(provider)
    if checker is None:
        if provider in _LOCAL_PROVIDERS:
            return _err("other", f"No checker for local provider {provider!r}")
        if not api_key:
            return _err(
                "auth_failed",
                f"No checker for {provider!r} and no API key provided",
            )
        return _ok_unvalidated(
            f"No free auth check available for {provider!r}; key saved.",
        )
    try:
        return checker(model=model, api_key=api_key)
    except requests.exceptions.Timeout:
        return _err("network", f"Connection to {provider} timed out")
    except requests.exceptions.ConnectionError as exc:
        return _err("network", f"Network error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected error checking %s", provider)
        return _err("other", f"Unexpected error: {exc}")


def _check_anthropic(model: str, api_key: str) -> Dict[str, Any]:
    if not api_key:
        return _err("auth_failed", "API key is empty")
    r = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": _USER_AGENT,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    return _match_model(r, model, key="id", provider_label="Anthropic")


def _check_openai(model: str, api_key: str) -> Dict[str, Any]:
    if not api_key:
        return _err("auth_failed", "API key is empty")
    r = requests.get(
        "https://api.openai.com/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    return _match_model(r, model, key="id", provider_label="OpenAI")


def _check_gemini(model: str, api_key: str) -> Dict[str, Any]:
    """Gemini returns model names as ``models/<id>``; strip the prefix
    before matching against the user-entered value."""
    if not api_key:
        return _err("auth_failed", "API key is empty")
    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT_SECONDS,
    )
    if r.status_code in (401, 403):
        return _err("auth_failed", f"HTTP {r.status_code}: invalid API key")
    if r.status_code != 200:
        return _err("other", f"Unexpected HTTP {r.status_code} from Gemini")
    payload = _safe_json(r) or {}
    names = [
        (m.get("name") or "").removeprefix("models/")
        for m in (payload.get("models") or [])
    ]
    if model in names or _matches_alias(model, names):
        return _ok(f"{model} available on Gemini")
    return _err("not_found", f"Model {model!r} not found in Gemini")


def _check_ollama(model: str, api_key: str) -> Dict[str, Any]:
    """Local provider — no key needed. Confirms reachability and that
    the model has been pulled. Ollama tags often include a ``:latest``
    suffix, so accept both forms."""
    base = "http://localhost:11434"
    r = requests.get(
        f"{base}/api/tags",
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT_SECONDS,
    )
    if r.status_code != 200:
        return _err("other", f"Unexpected HTTP {r.status_code} from {base}")
    payload = _safe_json(r) or {}
    names = [m.get("name", "") for m in (payload.get("models") or [])]
    if model in names or f"{model}:latest" in names:
        return _ok(f"{model} available locally")
    return _err(
        "not_found",
        f"Model {model!r} not pulled in Ollama at {base}",
    )


_CHECKERS = {
    "anthropic": _check_anthropic,
    "openai": _check_openai,
    "gemini": _check_gemini,
    "ollama": _check_ollama,
    "ollama_chat": _check_ollama,
}


# ── Backend reachability checks ────────────────────────────────────


def _check_backends(settings: Settings) -> List[BackendResult]:
    """Run the configured backend probes sequentially. Used by tests and
    sync callers; ``validate()`` fans the same probes out in parallel.
    Both draw the active backends from the shared registry."""
    from app.settings.backends import active_backend_checks
    return [c.status_probe(settings) for c in active_backend_checks(settings)]


def _check_brave_configured(settings: Settings) -> BackendResult:
    if not settings.brave_api_key:
        return BackendResult(
            backend="web-search",
            label="Brave Search",
            ok=False,
            error_kind="missing_credential",
            detail="Web search backend is Brave but BRAVE_API_KEY is empty",
        )
    return BackendResult(
        backend="web-search",
        label="Brave Search",
        ok=True,
        detail="Configured (no free auth check — first query will validate)",
    )


def _check_tavily_configured(settings: Settings) -> BackendResult:
    if not settings.tavily_api_key:
        return BackendResult(
            backend="web-search",
            label="Tavily",
            ok=False,
            error_kind="missing_credential",
            detail="Web search backend is Tavily but TAVILY_API_KEY is empty",
        )
    return BackendResult(
        backend="web-search",
        label="Tavily",
        ok=True,
        detail="Configured (no free auth check — first query will validate)",
    )


def _retry_transient(probe: Callable[[], BackendResult]) -> BackendResult:
    """Run ``probe`` once, retrying on transient network failures — but
    only inside Docker, where a sibling service may still be coming up
    (the compose co-start race). Native/bundle does a single attempt: a
    down backend won't self-resolve mid-boot, so retrying would just
    freeze startup. Config / protocol failures (``missing_credential``,
    ``other``) never retry.
    """
    result = probe()
    if not _running_in_docker():
        return result
    attempt = 1
    while (
        not result.ok
        and result.error_kind == "network"
        and attempt < _BACKEND_PROBE_ATTEMPTS
    ):
        log.debug(
            "Backend probe attempt %d/%d failed (%s) — retrying in %.1fs",
            attempt, _BACKEND_PROBE_ATTEMPTS, result.detail,
            _BACKEND_PROBE_BACKOFF_SECONDS,
        )
        time.sleep(_BACKEND_PROBE_BACKOFF_SECONDS)
        result = probe()
        attempt += 1
    return result


def _probe_searxng(settings: Settings) -> BackendResult:
    url = (settings.searxng_url or "").strip().rstrip("/")
    if not url:
        return BackendResult(
            backend="web-search",
            label="SearXNG",
            ok=False,
            error_kind="missing_credential",
            detail="Web search backend is SearXNG but SEARXNG_URL is empty",
        )
    return _retry_transient(lambda: _probe_searxng_once(url))


def _probe_searxng_once(url: str) -> BackendResult:
    try:
        r = requests.get(
            f"{url}/", headers={"User-Agent": _USER_AGENT},
            timeout=_BACKEND_PROBE_TIMEOUT_SECONDS, allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        return BackendResult(
            backend="web-search", label="SearXNG", ok=False,
            error_kind="network", detail=f"Connection to {url} timed out",
        )
    except requests.exceptions.ConnectionError as exc:
        return BackendResult(
            backend="web-search", label="SearXNG", ok=False,
            error_kind="network", detail=f"Network error: {exc}",
        )
    if r.status_code == 200:
        return BackendResult(
            backend="web-search", label="SearXNG", ok=True,
            detail=f"Reachable at {url}",
        )
    return BackendResult(
        backend="web-search", label="SearXNG", ok=False,
        error_kind="other",
        detail=f"Unexpected HTTP {r.status_code} from {url}",
    )


def probe_tts(settings: Settings) -> BackendResult:
    """TCP-connect probe for the F5-TTS socket server. A SYN-ACK on
    the configured port is enough — running the full TTS handshake
    as a healthcheck would synthesise audio we'd discard."""
    from tts.url import TtsUrlError, parse_tts_url

    raw = settings.tts_url or ""
    try:
        host, port = parse_tts_url(raw)
    except TtsUrlError as exc:
        return BackendResult(
            backend="tts", label="F5-TTS server", ok=False,
            error_kind="other", detail=str(exc),
        )
    label = f"{host}:{port}"
    return _retry_transient(lambda: _probe_tts_once(host, port, label))


def _probe_tts_once(host: str, port: int, label: str) -> BackendResult:
    import socket

    try:
        with socket.create_connection((host, port), timeout=_BACKEND_PROBE_TIMEOUT_SECONDS):
            pass
    except socket.timeout:
        return BackendResult(
            backend="tts", label="F5-TTS server", ok=False,
            error_kind="network", detail=f"Connection to {label} timed out",
        )
    except OSError as exc:
        return BackendResult(
            backend="tts", label="F5-TTS server", ok=False,
            error_kind="network", detail=f"Connection refused: {exc}",
        )
    return BackendResult(
        backend="tts", label="F5-TTS server", ok=True,
        detail=f"Reachable at {label}",
    )


# ── Helpers ────────────────────────────────────────────────────────


_SNAPSHOT_SUFFIX_RE = re.compile(r"-\d{8}$")


def _matches_alias(model: str, names: List[str]) -> bool:
    """Accept a name that's the dated snapshot of ``model``.

    Anthropic's /v1/models lists versioned IDs (``…-20251001``) but
    accepts the un-dated alias (``claude-haiku-4-5``) at the messages
    API. A user typing the alias should still validate.
    """
    prefix = f"{model}-"
    for name in names:
        if name.startswith(prefix) and _SNAPSHOT_SUFFIX_RE.search(name):
            return True
    return False


def _match_model(
    response: requests.Response,
    model: str,
    *,
    key: str,
    provider_label: str,
) -> Dict[str, Any]:
    """Common ``/v1/models``-style response handler used by Anthropic
    and OpenAI."""
    if response.status_code in (401, 403):
        return _err(
            "auth_failed",
            f"HTTP {response.status_code}: invalid API key",
        )
    if response.status_code != 200:
        return _err(
            "other",
            f"Unexpected HTTP {response.status_code} from {provider_label}",
        )
    payload = _safe_json(response) or {}
    names = [m.get(key, "") for m in (payload.get("data") or [])]
    if model in names or _matches_alias(model, names):
        return _ok(f"{model} available on {provider_label}")
    return _err("not_found", f"Model {model!r} not found in {provider_label}")


def _safe_json(response: requests.Response) -> Optional[Dict[str, Any]]:
    try:
        return response.json()
    except ValueError:
        return None


def _ok(detail: str) -> Dict[str, Any]:
    return {"ok": True, "detail": detail}


def _ok_unvalidated(detail: str) -> Dict[str, Any]:
    return {"ok": True, "not_validated": True, "detail": detail}


def _err(kind: str, detail: str) -> Dict[str, Any]:
    return {"ok": False, "error_kind": kind, "detail": detail}


# ── Process-wide singleton ─────────────────────────────────────────


_validator: Optional[ConfigValidator] = None


def get_validator() -> ConfigValidator:
    """Return the process-wide ``ConfigValidator``.

    Boot, WS save handlers, and the validation-status broadcaster all
    share one instance so that pending-restart state and the last
    validation result are visible across call sites.
    """
    global _validator
    if _validator is None:
        _validator = ConfigValidator()
    return _validator


def set_broadcast(callback: Optional[Callable[[Dict[str, Any]], Any]]) -> None:
    """Wire (or rewire) the broadcast callback on the singleton.
    Called by ``swarpius.py`` after the WS server has bound."""
    get_validator()._broadcast = callback


def reset_validator_for_tests() -> None:
    """Drop the singleton so the next ``get_validator()`` call returns
    a fresh instance. The autouse fixture in ``tests/conftest.py``
    should call this between tests."""
    global _validator
    _validator = None
