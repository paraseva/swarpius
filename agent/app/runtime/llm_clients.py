"""Per-agent LLM client construction and ownership.

Owns the three :class:`LLMClient` instances the agent runs
(coordinator, arbiter, diagnostic) plus their resolved profiles.
Extracted from ``RuntimeState`` so the LLM-setup concern lives in
one focused place: a contributor adding a fourth agent edits this
file, not the runtime state class.

``RuntimeState`` exposes the clients via property delegation
(``runtime.llm_client`` → ``runtime.llm_clients.coordinator`` etc.)
so existing callers keep working unchanged — the state lives here,
the surface stays familiar.
"""

from __future__ import annotations

from dataclasses import fields as dc_fields
from typing import Callable, Optional

from app.llm.client import LLMClient
from app.llm.model_profiles import ModelProfile, ResolvedProfile


def _classifier_temperature(
    profile_temperature: Optional[float], *, locked: bool = False,
) -> Optional[float]:
    """Re-export via lazy import to avoid the import cycle (the
    helper module imports nothing from here, but this module is
    imported by state.py at top level)."""
    from app.runtime.state_helpers import _classifier_temperature as impl
    return impl(profile_temperature, locked=locked)


class LLMClientsManager:
    """Holds the coordinator + arbiter + diagnostic LLM clients."""

    def __init__(self) -> None:
        self.coordinator: Optional[LLMClient] = None
        self.arbiter: Optional[LLMClient] = None
        self.diagnostic: Optional[LLMClient] = None
        self.coordinator_model_profile: Optional[ModelProfile] = None
        self.coordinator_resolved: Optional[ResolvedProfile] = None
        self.arbiter_resolved: Optional[ResolvedProfile] = None
        self.diagnostic_resolved: Optional[ResolvedProfile] = None

    def reset(self) -> None:
        self.coordinator = None
        self.arbiter = None
        self.diagnostic = None
        self.coordinator_model_profile = None
        self.coordinator_resolved = None
        self.arbiter_resolved = None
        self.diagnostic_resolved = None

    def build(
        self,
        *,
        default_model: str,
        arbiter_spec: str,
        diagnostic_spec: str,
        parse_model_spec: Callable[[str], tuple],
        get_model_profile: Callable[..., ResolvedProfile],
        yaml_config: dict,
        log_callback: Callable[[ResolvedProfile], None],
    ) -> str:
        """Build all three clients. Returns the resolved coordinator
        model name for the caller's startup-summary line.

        ``parse_model_spec`` and ``get_model_profile`` are injected
        rather than imported here so tests patching them at
        ``app.runtime.state.<name>`` reach this construction code.
        """
        coordinator_spec = default_model

        coord_model, coord_key = parse_model_spec(coordinator_spec)
        resolved = get_model_profile(coord_model, yaml_config=yaml_config)
        self.coordinator_model_profile = resolved.model_profile
        self.coordinator_resolved = resolved
        log_callback(resolved)

        self.coordinator = LLMClient(
            model=coord_model, api_key=coord_key,
            temperature=resolved.temperature,
            top_p=resolved.top_p,
            generation_params=resolved.generation_params,
        )

        # Arbiter and diagnostic agents are classification jobs (queue
        # vs interrupt; which conversation thread does this belong to)
        # — pin temperature=0 regardless of the model's profile, so
        # they don't flicker between answers across runs. Coordinator
        # stays profile-driven (composition + tool selection, where
        # variance is fine).
        #
        # Always build dedicated clients here even when the model spec
        # matches the coordinator's — sharing the coordinator client
        # would mutate its temperature.
        if arbiter_spec == coordinator_spec:
            arb_model, arb_key = coord_model, coord_key
            arb_resolved = resolved
        else:
            arb_model, arb_key = parse_model_spec(arbiter_spec)
            arb_resolved = get_model_profile(arb_model, yaml_config=yaml_config)
        self.arbiter = LLMClient(
            model=arb_model, api_key=arb_key,
            temperature=_classifier_temperature(
                arb_resolved.temperature, locked=arb_resolved.temperature_lock,
            ),
            top_p=arb_resolved.top_p,
            generation_params=arb_resolved.generation_params,
        )
        self.arbiter_resolved = arb_resolved

        if diagnostic_spec == coordinator_spec:
            diag_model, diag_key = coord_model, coord_key
            diag_resolved = resolved
        elif diagnostic_spec == arbiter_spec:
            diag_model, diag_key = arb_model, arb_key
            diag_resolved = arb_resolved
        else:
            diag_model, diag_key = parse_model_spec(diagnostic_spec)
            diag_resolved = get_model_profile(diag_model, yaml_config=yaml_config)
        self.diagnostic = LLMClient(
            model=diag_model, api_key=diag_key,
            temperature=_classifier_temperature(
                diag_resolved.temperature, locked=diag_resolved.temperature_lock,
            ),
            top_p=diag_resolved.top_p,
            generation_params=diag_resolved.generation_params,
        )
        self.diagnostic_resolved = diag_resolved

        return coord_model


def format_profile_log_line(resolved: ResolvedProfile) -> str:
    """Build the "Coordinator profile [...]: ..." line. Pure
    formatting, exposed so the caller can put it through its own
    logger without re-implementing the loop."""
    knobs = ", ".join(
        f"{f.name}={getattr(resolved.model_profile, f.name)}"
        + ("" if f.name in resolved.explicit_knobs else " (default)")
        for f in dc_fields(resolved.model_profile)
    )
    if resolved.matched_pattern:
        return f"Coordinator profile [{resolved.matched_pattern}]: {knobs}"
    return f"Coordinator profile (no match): {knobs}"
