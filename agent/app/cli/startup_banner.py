"""Render the CLI startup banner — a single block printed once
after ``runtime.ensure_initialised`` so the operator can verify
key configuration at a glance instead of reading INFO log
chatter that's been routed to the file."""

from __future__ import annotations

from typing import Any, Dict, Optional

_LABEL_WIDTH = 16
_RULE_WIDTH = 64
_RULE = "─" * _RULE_WIDTH


def copyright_notice() -> str:
    return "Swarpius™ — Copyright © 2026 Paraseva Ltd"


def render_banner(facts: Dict[str, Any]) -> str:
    """Render a startup banner from the given facts dict.

    Expected keys (all required; pass ``None`` / ``False`` for
    "off"):
      roon_core, roon_profile, default_zone,
      coordinator_model, coordinator_pattern (Optional[str]),
      arbiter_model, diagnostic_model,
      diagnostic_enabled (bool), prompt_caching (bool),
      web_search (Optional[str]),  # provider description or None
      tts_url (Optional[str]),
      parallel_tools (bool), parallel_max (Optional[int]),
      log_file (Optional[Path|str]).
    """
    coord = facts["coordinator_model"]
    if facts.get("coordinator_pattern"):
        coord = f"{coord}  [{facts['coordinator_pattern']}]"

    diagnostic_value = facts["diagnostic_model"]
    diag_mode = "on" if facts["diagnostic_enabled"] else "off"

    web_search = facts.get("web_search") or "disabled"
    tts = facts.get("tts_url") or "disabled"

    if facts["parallel_tools"]:
        max_n = facts.get("parallel_max")
        if max_n is None:
            parallel = "on  (unlimited)"
        else:
            parallel = f"on  (max {max_n} concurrent)"
    else:
        parallel = "off"

    log_file = str(facts["log_file"]) if facts.get("log_file") else "disabled"

    sections = [
        [
            ("Roon Core", facts["roon_core"]),
            ("Roon profile", facts["roon_profile"]),
            ("Default zone", facts["default_zone"]),
        ],
        [
            ("Coordinator", coord),
            ("Arbiter", facts["arbiter_model"]),
            ("Diagnostic", f"{diagnostic_value}  (mode: {diag_mode})"),
            ("Prompt caching", "on" if facts["prompt_caching"] else "off"),
        ],
        [
            ("Web search", web_search),
            ("TTS", tts),
            ("Parallel tools", parallel),
        ],
        [
            ("Logs", log_file),
        ],
    ]

    lines: list[str] = [_RULE, "  Swarpius", _RULE]
    for i, section in enumerate(sections):
        if i > 0:
            lines.append("")
        for label, value in section:
            lines.append(f"  {label:<{_LABEL_WIDTH}}{value}")
    lines.append(_RULE)
    return "\n".join(lines)


def collect_banner_facts(
    runtime: Any,
    settings: Any,
    log_file: Optional[Any],
) -> Dict[str, Any]:
    """Pull banner-worthy facts off the live runtime + settings.

    Defensive about missing attributes — runtime construction
    paths in tests don't always populate every client / profile.
    """
    roon_conn = runtime.roon_connection
    roon_api = getattr(roon_conn, "api", None)
    host = getattr(roon_conn, "roon_core_host", None) or getattr(roon_api, "host", None)
    port = getattr(roon_conn, "roon_core_port", None)
    name = getattr(roon_api, "core_name", "") or ""
    if host:
        addr = f"{host}:{port}" if port else str(host)
        roon_core = f"{name} ({addr})" if name else addr
    else:
        roon_core = "(not connected)"

    coordinator_model = getattr(runtime.llm_client, "model", "(unknown)")
    arbiter_model = getattr(runtime.arbiter_client, "model", coordinator_model)
    diagnostic_model = getattr(runtime.diagnostic_client, "model", coordinator_model)

    coordinator_pattern = None
    rp = getattr(runtime, "resolved_profile", None)
    if rp is not None:
        coordinator_pattern = getattr(rp, "matched_pattern", None) or None

    from app.coordinator.request_flow import is_prompt_caching_enabled
    from app.llm.diagnostic_agent import is_diagnostic_agent_enabled

    web_search_provider = None
    web_search_tool = runtime.tool_registry.get("web_search")
    if web_search_tool is not None and web_search_tool.tool_instance is not None:
        web_search_provider = getattr(
            web_search_tool.tool_instance, "provider_name", "enabled",
        )

    parallel_max = getattr(settings, "roon_max_parallel", None)
    if parallel_max is not None and parallel_max < 1:
        parallel_max = None  # 0 / negative → unlimited

    return {
        "roon_core": roon_core,
        "roon_profile": settings.roon_profile_name or "(default)",
        "default_zone": settings.default_roon_zone or "(unset)",
        "coordinator_model": coordinator_model,
        "coordinator_pattern": coordinator_pattern,
        "arbiter_model": arbiter_model,
        "diagnostic_model": diagnostic_model,
        "diagnostic_enabled": is_diagnostic_agent_enabled(),
        "prompt_caching": is_prompt_caching_enabled(),
        "web_search": web_search_provider,
        "tts_url": settings.tts_url or None,
        "parallel_tools": bool(getattr(settings, "parallel_tools", False)),
        "parallel_max": parallel_max,
        "log_file": log_file,
    }
