"""Low-level LLM machinery for the passive analyser.

Contains the discriminated result shape, error classification, model
tuning lookup, LiteLLM invocation, and JSON extraction.  Workflow
functions that *use* this layer (batch analysis, lesson extraction,
lesson refinement, lesson consolidation) stay in ``analyse.py`` because
they glue these calls to storage and formatting.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    import litellm
except ImportError:
    litellm = None  # type: ignore[assignment]

# The agent's model-profiles loader is a sibling package now that the
# analyser lives inside agent/. Degrade gracefully if the import path
# ever shifts — the analyser still functions without profile tuning.
try:
    from app.llm.error_classification import classify_llm_error, is_permanent
    from app.llm.model_profiles import (
        get_model_profile,
        load_yaml_profiles_with_override,
    )
except Exception:  # noqa: BLE001 — degrade gracefully if the import path breaks
    get_model_profile = None  # type: ignore[assignment]
    load_yaml_profiles_with_override = None  # type: ignore[assignment]

    def classify_llm_error(_exc, **_kwargs):  # type: ignore[misc]
        return "transient"

    def is_permanent(_kind) -> bool:  # type: ignore[misc]
        return False

# The user override lives in the persistent data dir so it survives bundle
# upgrades; degrade to no override if the resolver isn't importable.
try:
    from app.data_paths import data_dir as _data_dir
except Exception:  # noqa: BLE001
    _data_dir = None  # type: ignore[assignment]

MODEL_PROFILES_PATH = Path(__file__).resolve().parent.parent / "model_profiles.yaml"

_log = logging.getLogger("analyse.llm")

_tuning_cache: dict[str, dict] = {}


def _resolve_model_tuning(model: str) -> dict:
    """Resolve generation params for an analyser LLM call.

    The analyser is a classification job, not creative generation —
    we force ``temperature=0`` regardless of what the model's profile
    says, so that re-analysing the same conversation produces the
    same findings (eliminates non-deterministic flips on Re-Analyse).

    The profile's other params (``top_p``, ``generation_params``)
    still carry through, and an explicit ``temperature: null`` in the
    profile is preserved (means "model deprecated the param, omit it").
    drop_params=True on the call site catches any remaining
    provider-side rejections too.

    Logs the resolved profile once per unique model for observability,
    mirroring how the main agent announces its coordinator profile at init.
    """
    if model in _tuning_cache:
        return _tuning_cache[model]

    if get_model_profile is None or load_yaml_profiles_with_override is None:
        tuning = {"temperature": 0.0}
        _log.warning(
            "Analyser profile system unavailable for %s — check that "
            "agent/app/ and agent/model_profiles.yaml are reachable "
            "(sys.path, Docker bind mounts). Falling back to %s.",
            model, tuning,
        )
        _tuning_cache[model] = tuning
        return tuning
    try:
        override = (_data_dir() / "model_profiles.yaml") if _data_dir else None
        config = load_yaml_profiles_with_override(MODEL_PROFILES_PATH, override)
        profile = get_model_profile(model, yaml_config=config)
    except Exception:  # noqa: BLE001
        tuning = {"temperature": 0.0}
        _log.warning(
            "Analyser profile resolution failed for %s — falling back to %s",
            model, tuning,
        )
        _tuning_cache[model] = tuning
        return tuning
    tuning: dict = {}
    # Pin temperature to 0 unless the profile explicitly omits it
    # (``temperature: null`` — model deprecated the param) or marks
    # it locked (``temperature_lock: true`` — e.g. GPT-5 rejects any
    # value other than 1.0; drop_params can't help here because the
    # param IS recognised, it's the value the model rejects).
    if profile.temperature is not None:
        tuning["temperature"] = (
            profile.temperature if profile.temperature_lock else 0.0
        )
    if profile.top_p is not None:
        tuning["top_p"] = profile.top_p
    tuning.update(profile.generation_params)
    if profile.matched_pattern:
        _log.info(
            "Analyser profile [%s] for %s: %s",
            profile.matched_pattern, model, tuning,
        )
    else:
        _log.info(
            "Analyser profile (no match) for %s: %s",
            model, tuning,
        )
    _tuning_cache[model] = tuning
    return tuning


ErrorKind = Literal["transient", "permanent", "input_shape"]


@dataclass
class CompletionResult:
    """Discriminated result of ``llm_completion``.

    On success, ``text`` carries the LLM response and ``error_kind`` is
    None. On failure, ``text`` is None and ``error_kind`` classifies
    the error so callers can decide how to react:
      - ``transient``   — rate-limit, timeout: retry with backoff
      - ``permanent``   — auth / misconfig: fail fast, don't retry
      - ``input_shape`` — context too large: reshape input (truncate)
                          rather than retrying as-is
    """
    text: str | None = None
    error_kind: ErrorKind | None = None
    detail: str | None = None


def _classify_llm_error(exc: BaseException) -> ErrorKind:
    """Map the shared error classification onto the analyser's retry/halt
    taxonomy: context-length → reshape the input; auth / not-found /
    bad-request → halt (permanent, won't fix by retrying); everything
    else → retry (transient). Classification itself is centralised in
    ``app.llm.error_classification`` so every agent agrees.
    """
    kind = classify_llm_error(exc)
    if kind == "context_length":
        return "input_shape"
    if is_permanent(kind):
        return "permanent"
    return "transient"


def llm_completion(
    model: str,
    api_key: str,
    system: str,
    user_message: str,
    max_tokens: int = 4096,
) -> CompletionResult:
    """Make a synchronous LLM completion call via LiteLLM.

    Returns a :class:`CompletionResult`: ``text`` is populated on
    success; on failure ``error_kind`` discriminates transient,
    permanent, and input-shape errors so callers can distinguish
    retry-worthy failures from misconfiguration.
    """
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "drop_params": True,
    }
    kwargs.update(_resolve_model_tuning(model))
    if api_key:
        kwargs["api_key"] = api_key

    # Suppress litellm's verbose logging
    litellm.success_callback = []
    litellm.failure_callback = []

    try:
        response = litellm.completion(**kwargs)
        text = response.choices[0].message.content if response.choices else None
        return CompletionResult(text=text)
    except Exception as e:
        kind = _classify_llm_error(e)
        _log.error("LLM completion error (%s): %s", kind, e)
        return CompletionResult(error_kind=kind, detail=str(e))


def parse_json_response(text: str) -> dict | list | None:
    """Extract JSON (object or array) from the model's response."""
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall through to the markdown-code-block extractor below.
        pass

    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            # Fall through to the boundary-scan strategy below.
            pass

    for open_ch, close_ch in [("[", "]"), ("{", "}")]:
        start = text.find(open_ch)
        if start >= 0:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break

    return None
