from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    # Provider-agnostic approximation commonly used for rough budgeting.
    return max(1, len(text) // 4)


def _dump_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, default=str)
    except Exception:
        return str(value)


def _as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            # Best-effort extraction across heterogeneous LLM response
            # shapes — fall through to __dict__ if model_dump misbehaves.
            pass
    if hasattr(value, "__dict__"):
        try:
            dumped = vars(value)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return None
    return None


def _extract_usage_candidate(mapping: Dict[str, Any]) -> tuple[int, Optional[int], Optional[int], Optional[int]]:
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    input_tokens = _safe_int(lowered.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _safe_int(lowered.get("prompt_tokens"))
    output_tokens = _safe_int(lowered.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _safe_int(lowered.get("completion_tokens"))
    total_tokens = _safe_int(lowered.get("total_tokens"))

    score = 0
    if input_tokens is not None:
        score += 2
    if output_tokens is not None:
        score += 2
    if total_tokens is not None:
        score += 1
    return score, input_tokens, output_tokens, total_tokens


def _extract_usage_from_sources(*sources: Any) -> tuple[Optional[int], Optional[int], Optional[int], str]:
    best_score = -1
    best_values: tuple[Optional[int], Optional[int], Optional[int]] = (None, None, None)
    visited: set[int] = set()
    queue: Deque[tuple[Any, int]] = deque((src, 0) for src in sources if src is not None)

    while queue:
        current, depth = queue.popleft()
        if depth > 5:
            continue
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)

        mapping = _as_mapping(current)
        if mapping is None:
            continue

        score, input_tokens, output_tokens, total_tokens = _extract_usage_candidate(mapping)
        if score > best_score:
            best_score = score
            best_values = (input_tokens, output_tokens, total_tokens)

        for key, value in mapping.items():
            key_lower = str(key).lower()
            if key_lower in {
                "usage",
                "token_usage",
                "response_usage",
                "raw_response",
                "response",
                "completion",
                "last_response",
                "llm_response",
            }:
                queue.append((value, depth + 1))
            elif isinstance(value, dict):
                queue.append((value, depth + 1))

    if best_score <= 0:
        return None, None, None, "estimated"
    return best_values[0], best_values[1], best_values[2], "provider"


def _estimate_context_provider_tokens(agent: Any) -> tuple[int, List[Dict[str, Any]]]:
    provider_map = None
    for attr_name in (
        "context_providers",
        "_context_providers",
        "dynamic_context_providers",
        "_dynamic_context_providers",
    ):
        value = getattr(agent, attr_name, None)
        if isinstance(value, dict):
            provider_map = value
            break

    if not isinstance(provider_map, dict):
        return 0, []

    breakdown: List[Dict[str, Any]] = []
    total = 0
    for provider_name, provider in provider_map.items():
        if provider is None:
            continue
        get_info = getattr(provider, "get_info", None)
        if not callable(get_info):
            continue
        try:
            info_text = str(get_info() or "")
        except Exception:
            info_text = ""
        if not info_text:
            continue
        token_estimate = _estimate_tokens_from_text(info_text)
        total += token_estimate
        breakdown.append(
            {
                "name": str(provider_name),
                "char_count": len(info_text),
                "estimated_tokens": token_estimate,
            },
        )
    return total, breakdown


def _estimate_system_prompt_tokens(agent: Any) -> int:
    system_prompt = ""
    config = getattr(agent, "config", None)
    generator = getattr(config, "system_prompt_generator", None) if config is not None else None
    generate_prompt = getattr(generator, "generate_prompt", None) if generator is not None else None
    if callable(generate_prompt):
        try:
            system_prompt = str(generate_prompt() or "")
        except Exception:
            system_prompt = ""
    if not system_prompt:
        direct_prompt = getattr(agent, "system_prompt", None) or getattr(agent, "_system_prompt", None)
        system_prompt = str(direct_prompt or "")
    return _estimate_tokens_from_text(system_prompt) if system_prompt else 0


def collect_prompt_diagnostics(agent: Any, agent_input: Any) -> Dict[str, Any]:
    input_text = _dump_text(_as_mapping(agent_input) or agent_input)
    input_tokens = _estimate_tokens_from_text(input_text)
    system_tokens = _estimate_system_prompt_tokens(agent)
    context_tokens, context_breakdown = _estimate_context_provider_tokens(agent)
    total_input = input_tokens + system_tokens + context_tokens
    return {
        "estimated_input_tokens": total_input,
        "input_schema_tokens_estimated": input_tokens,
        "system_prompt_tokens_estimated": system_tokens,
        "context_tokens_estimated": context_tokens,
        "context_breakdown": sorted(
            context_breakdown,
            key=lambda item: int(item.get("estimated_tokens") or 0),
            reverse=True,
        ),
    }


def collect_usage_metrics(
    agent: Any,
    agent_input: Any,
    agent_output: Any,
    prompt_diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    input_tokens, output_tokens, total_tokens, source = _extract_usage_from_sources(agent_output, agent)
    prompt_diag = prompt_diagnostics or collect_prompt_diagnostics(agent=agent, agent_input=agent_input)
    estimated_input = int(prompt_diag.get("estimated_input_tokens") or 0)
    estimated_output = _estimate_tokens_from_text(_dump_text(_as_mapping(agent_output) or agent_output))

    if input_tokens is None:
        input_tokens = estimated_input
        source = "estimated_prompt" if source == "estimated" else "provider+estimated_fill"
    if output_tokens is None:
        output_tokens = estimated_output
        if source in {"estimated", "estimated_prompt"}:
            source = "estimated_prompt"
        else:
            source = "provider+estimated_fill"
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
        if source == "provider":
            source = "provider+derived_total"

    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total_tokens),
        "source": source,
    }


def estimate_prompt_tokens_for_input(agent_input: Any, agent: Any = None) -> int:
    if agent is None:
        return _estimate_tokens_from_text(_dump_text(_as_mapping(agent_input) or agent_input))
    diagnostics = collect_prompt_diagnostics(agent=agent, agent_input=agent_input)
    return int(diagnostics.get("estimated_input_tokens") or 0)


@dataclass
class _UsageEvent:
    timestamp: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    outcome: str
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0


class UsageTracker:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0
        self.retry_input_tokens = 0
        self.retry_total_tokens = 0
        self.success_input_tokens = 0
        self.success_output_tokens = 0
        self.success_total_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cost_usd: float = 0.0
        self.events: Deque[_UsageEvent] = deque()

    def _evict_old_events(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()

    def record(
        self,
        *,
        agent_name: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        source: str,
        outcome: str = "success",
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cost_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        cost_value = float(cost_usd) if cost_usd is not None else 0.0
        now = time.time()
        event = _UsageEvent(
            timestamp=now,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            outcome=outcome,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cost_usd=cost_value,
        )
        self.events.append(event)
        self._evict_old_events(now)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += total_tokens
        self.total_cache_creation_tokens += cache_creation_input_tokens
        self.total_cache_read_tokens += cache_read_input_tokens
        self.total_cost_usd += cost_value
        if outcome == "success":
            self.success_input_tokens += input_tokens
            self.success_output_tokens += output_tokens
            self.success_total_tokens += total_tokens
        elif outcome == "rate_limited_retry":
            self.retry_input_tokens += input_tokens
            self.retry_total_tokens += total_tokens

        minute_input = sum(evt.input_tokens for evt in self.events)
        minute_output = sum(evt.output_tokens for evt in self.events)
        minute_total = sum(evt.total_tokens for evt in self.events)
        minute_requests = len(self.events)
        minute_retry_input = sum(
            evt.input_tokens for evt in self.events if evt.outcome == "rate_limited_retry"
        )
        minute_retry_total = sum(
            evt.total_tokens for evt in self.events if evt.outcome == "rate_limited_retry"
        )
        minute_success_total = sum(
            evt.total_tokens for evt in self.events if evt.outcome == "success"
        )
        minute_cache_read = sum(evt.cache_read_input_tokens for evt in self.events)

        return {
            "agent": agent_name,
            "source": source,
            "outcome": outcome,
            "call": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "outcome": outcome,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "cost_usd": cost_value,
            },
            "session_totals": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_tokens,
                "cache_creation_input_tokens": self.total_cache_creation_tokens,
                "cache_read_input_tokens": self.total_cache_read_tokens,
                "cost_usd": self.total_cost_usd,
            },
            "session_breakdown": {
                "success_input_tokens": self.success_input_tokens,
                "success_output_tokens": self.success_output_tokens,
                "success_total_tokens": self.success_total_tokens,
                "rate_limited_retry_input_tokens_estimated": self.retry_input_tokens,
                "rate_limited_retry_total_tokens_estimated": self.retry_total_tokens,
            },
            "tokens_per_minute": {
                "window_seconds": self.window_seconds,
                "input_tokens": minute_input,
                "output_tokens": minute_output,
                "total_tokens": minute_total,
                "cache_read_input_tokens": minute_cache_read,
            },
            "tokens_per_minute_breakdown": {
                "window_seconds": self.window_seconds,
                "success_total_tokens": minute_success_total,
                "rate_limited_retry_input_tokens_estimated": minute_retry_input,
                "rate_limited_retry_total_tokens_estimated": minute_retry_total,
            },
            "requests_per_minute": {
                "window_seconds": self.window_seconds,
                "request_count": minute_requests,
            },
        }
