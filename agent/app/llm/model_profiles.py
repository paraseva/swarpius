"""Model profiles and YAML-based tuning configuration.

Provides YAML config loading for per-model/provider tuning: temperature,
sampling params, generation flags, and coordinator loop limits.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("swarpius.model_profiles")


# ---------------------------------------------------------------------------
# Model profile dataclass (per-model runtime knobs)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelProfile:
    """Per-model runtime knobs for the coordinator loop.

    Configurable via YAML profile fields.  Models without an explicit
    profile get these defaults, which match the current hardcoded values.
    """

    max_coordinator_steps: int = 12
    soft_nudge_step: int = 8


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------

def load_yaml_profiles(path: Path) -> Dict[str, Any]:
    """Load model profiles from a YAML config file.

    Returns a dict with ``defaults`` and ``profiles`` keys.
    If the file is missing or empty, returns safe empty defaults.
    """
    if not path.exists():
        _log.debug("Model profiles config not found at %s — using defaults", path)
        return {"defaults": {}, "profiles": []}

    try:
        import yaml
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {"defaults": {}, "profiles": []}
        data = yaml.safe_load(text) or {}
        return {
            "defaults": data.get("defaults") or {},
            "profiles": data.get("profiles") or [],
        }
    except Exception:
        _log.warning("Failed to load model profiles from %s — using defaults", path, exc_info=True)
        return {"defaults": {}, "profiles": []}


def load_yaml_profiles_with_override(
    bundled_path: Path, override_path: Optional[Path],
) -> Dict[str, Any]:
    """Load bundled profiles, then layer a user override file over them.

    User ``profiles`` entries are placed ahead of the bundled ones, so a
    user pattern is matched first (``get_model_profile`` takes the first
    match). User ``defaults`` override the bundled ``defaults`` key by key.
    The override lives in the persistent data dir so it survives bundle
    upgrades; when absent or empty the bundled config is used unchanged.
    """
    base = load_yaml_profiles(bundled_path)
    if override_path is None or not override_path.exists():
        return base
    override = load_yaml_profiles(override_path)
    return {
        "defaults": {**base["defaults"], **override["defaults"]},
        "profiles": list(override["profiles"]) + list(base["profiles"]),
    }


# ---------------------------------------------------------------------------
# Resolved profile (YAML config + per-model runtime knobs)
# ---------------------------------------------------------------------------

@dataclass
class ResolvedProfile:
    """Combined result of YAML config matching.

    Carries the generation parameters (temperature, top_p, etc.) from the
    YAML config alongside the ModelProfile for runtime knobs.
    """

    model_profile: ModelProfile
    temperature: Optional[float] = 0.0
    top_p: Optional[float] = None
    generation_params: Dict[str, Any] = field(default_factory=dict)
    matched_pattern: Optional[str] = None
    explicit_knobs: frozenset[str] = field(default_factory=frozenset)
    # Set to True for models that require a specific temperature value
    # (e.g. GPT-5 family rejects anything other than 1.0). Classifier
    # paths (analyser, arbiter, diagnostic agent) skip the override-to-0
    # for these so the call doesn't error.
    temperature_lock: bool = False


def _match_profile(
    model_name: str, yaml_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Find the first matching profile entry for a model string."""
    for entry in yaml_config.get("profiles", []):
        pattern = entry.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, model_name, re.IGNORECASE):
                return entry
        except re.error:
            _log.warning("Invalid regex in model profile: %r", pattern)
    return None


def get_model_profile(
    model_name: str,
    *,
    yaml_config: Optional[Dict[str, Any]] = None,
) -> ResolvedProfile:
    """Resolve the full profile for a model.

    Parameters
    ----------
    model_name :
        Full LiteLLM model string, e.g. ``"ollama_chat/gemma4:26b"``.
    yaml_config :
        Loaded YAML config from :func:`load_yaml_profiles`.  If ``None``,
        only defaults are used.
    """
    config = yaml_config or {"defaults": {}, "profiles": []}
    defaults = config.get("defaults", {})
    matched = _match_profile(model_name, config)

    profile_kwargs: Dict[str, Any] = {}
    if matched:
        if "max_coordinator_steps" in matched:
            profile_kwargs["max_coordinator_steps"] = int(matched["max_coordinator_steps"])
        if "soft_nudge_step" in matched:
            profile_kwargs["soft_nudge_step"] = int(matched["soft_nudge_step"])
    model_profile = ModelProfile(**profile_kwargs)

    # Resolve temperature: profile > defaults > 0.0.
    # Explicit `null` preserved — means "omit from the request" for models
    # that deprecate the param (e.g. claude-opus-4-7).
    temperature: Optional[float] = 0.0
    if "temperature" in defaults:
        val = defaults["temperature"]
        temperature = None if val is None else float(val)
    if matched and "temperature" in matched:
        val = matched["temperature"]
        temperature = None if val is None else float(val)

    top_p = defaults.get("top_p")
    if matched and "top_p" in matched:
        top_p = matched["top_p"]
    if top_p is not None:
        top_p = float(top_p)

    gen_params: Dict[str, Any] = dict(defaults.get("generation_params") or {})
    if matched and matched.get("generation_params"):
        gen_params.update(matched["generation_params"])

    temperature_lock = bool(defaults.get("temperature_lock", False))
    if matched and "temperature_lock" in matched:
        temperature_lock = bool(matched["temperature_lock"])

    return ResolvedProfile(
        model_profile=model_profile,
        temperature=temperature,
        top_p=top_p,
        generation_params=gen_params,
        matched_pattern=matched.get("pattern") if matched else None,
        explicit_knobs=frozenset(profile_kwargs),
        temperature_lock=temperature_lock,
    )
