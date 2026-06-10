"""Thin LLM client wrapping LiteLLM for native tool-calling.

Provides a single async ``completion`` method that sends messages + tool
definitions to any LiteLLM-supported provider and returns a normalised
response.  Rate-limit retry logic lives here so callers don't have to.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_log = logging.getLogger("swarpius.llm_client")

_LITELLM_CONFIGURED = False

# Routine LLM-provider error classes. Failures of this kind get
# logged as a single line; the exception message already carries the
# useful detail and the LiteLLM transport stack adds noise without
# helping diagnosis. Unknown exceptions still log with a traceback.
_KNOWN_LLM_EXCEPTION_NAMES = frozenset({
    "Timeout",
    # Bare TimeoutError (e.g. the diagnostic agent's asyncio.wait_for budget) — routine.
    "TimeoutError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "APIConnectionError",
    "APIError",
    "AuthenticationError",
    "NotFoundError",
    "BadRequestError",
    "ContextWindowExceededError",
    "ContentPolicyViolationError",
    "AnthropicError",
    "OpenAIError",
})


def is_known_llm_exception(exc: BaseException) -> bool:
    """True when the exception is a routine LLM-provider error whose
    full traceback adds no diagnostic value."""
    return type(exc).__name__ in _KNOWN_LLM_EXCEPTION_NAMES


def _configure_litellm_once(litellm: Any) -> None:
    """Clear LiteLLM's default success/failure callbacks exactly once per
    process. Previously these were wiped on every LLMClient.completion
    call, which would silently disrupt any observability plugin that
    registered a callback between two agent calls.

    Also disables LiteLLM's direct-stdout debug banner (the
    "Give Feedback / Get Help" + "LiteLLM.Info: …" lines it prints on
    every failure) — those bypass Python logging, so log-handler
    filtering can't suppress them; only this flag can.
    """
    global _LITELLM_CONFIGURED
    if _LITELLM_CONFIGURED:
        return
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm.suppress_debug_info = True
    _LITELLM_CONFIGURED = True


@dataclass
class ToolCall:
    """A single tool call extracted from the LLM response."""

    id: str
    name: str
    arguments: dict  # already parsed from JSON string


@dataclass
class LLMResponse:
    """Normalised response from a completion call."""

    text: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    duration_ms: Optional[int] = None
    raw: Any = None  # the original litellm response object

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


def _parse_tool_calls(message: Any) -> List[ToolCall]:
    """Extract tool calls from a LiteLLM response message."""
    raw_calls = getattr(message, "tool_calls", None)
    if not raw_calls:
        return []
    result = []
    for tc in raw_calls:
        fn = tc.function
        try:
            args = json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
        except (json.JSONDecodeError, TypeError):
            args = {}
        result.append(ToolCall(id=tc.id, name=fn.name, arguments=args or {}))
    return result


def _extract_nested_cached_tokens(usage: Any) -> int:
    """Read `prompt_tokens_details.cached_tokens` if present.

    OpenAI / Gemini / DeepSeek via LiteLLM surface cache hits under this
    nested shape rather than the Anthropic-style top-level field.
    `prompt_tokens_details` may be an object (attribute access) or dict
    (subscript access) depending on LiteLLM's normalisation.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    if isinstance(details, dict):
        return int(details.get("cached_tokens") or 0)
    return int(getattr(details, "cached_tokens", 0) or 0)


def _extract_usage(response: Any) -> Dict[str, int]:
    """Pull token usage from the response, with safe fallbacks.

    Cross-provider: Anthropic exposes cache reads at the top level as
    `cache_read_input_tokens`; OpenAI / Gemini / DeepSeek expose them
    under `prompt_tokens_details.cached_tokens`. Prefer the Anthropic
    field when present, fall back to the nested field otherwise.
    Cache CREATION is Anthropic-only and stays 0 elsewhere.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    if not cache_read:
        cache_read = _extract_nested_cached_tokens(usage)
    return {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": cache_read,
    }


class LLMClient:
    """Stateless LLM client backed by LiteLLM.

    Parameters
    ----------
    model : str
        LiteLLM model identifier, e.g. ``"openai/gpt-4o-mini"`` or
        ``"anthropic/claude-haiku-4-5-20251001"``.
    api_key : str
        Provider API key.
    temperature : float | None
        Default sampling temperature (from model profile config). ``None``
        omits the param from the request — used for models that deprecate it
        (e.g. claude-opus-4-7).
    top_p : float | None
        Nucleus sampling threshold (from model profile config).
    generation_params : dict
        Extra kwargs passed to every LLM call (e.g. ``think: false``).
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: Optional[float] = 0.0,
        top_p: Optional[float] = None,
        generation_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.top_p = top_p
        self.generation_params = generation_params or {}

    async def completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
    ) -> LLMResponse:
        """Send a chat completion request and return a normalised response.

        Parameters
        ----------
        messages :
            OpenAI-style message list (system / user / assistant / tool roles).
        tools :
            Tool schemas as returned by ``ToolRegistry.to_tool_schemas()``.
            Pass ``None`` or ``[]`` to disable tool calling for this turn.

        Returns
        -------
        LLMResponse
            Contains either ``text`` (model replied) or ``tool_calls``
            (model wants to call tools), plus usage info.
        """
        from app.settings import get_settings
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
            "drop_params": True,
            "timeout": get_settings().llm_timeout_seconds,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if tools:
            kwargs["tools"] = tools

        # Apply profile-driven generation params (e.g. think=False for Ollama)
        kwargs.update(self.generation_params)

        _log.debug(
            "LLM request: model=%s messages=%d tools=%d",
            self.model, len(messages), len(tools) if tools else 0,
        )

        import litellm  # lazy import — keeps dataclasses importable without litellm
        _configure_litellm_once(litellm)

        started = time.perf_counter()
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            _log.warning(
                "LLM call failed: model=%s exception=%s: %s",
                self.model, type(exc).__name__, exc,
                exc_info=not is_known_llm_exception(exc),
            )
            _maybe_flag_runtime_failure(self.model, exc, litellm)
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)

        message = response.choices[0].message
        tool_calls = _parse_tool_calls(message)
        text = message.content if not tool_calls else None
        usage = _extract_usage(response)
        # Provider-correct cost (accounts for cache write premiums and
        # read discounts across Anthropic, OpenAI, Gemini, DeepSeek).
        try:
            cost = litellm.completion_cost(completion_response=response, model=self.model)
            if cost is not None:
                usage["cost_usd"] = float(cost)
        except Exception:  # noqa: BLE001 — unknown model, missing price map, etc.
            pass

        _log.debug(
            "LLM response: duration=%dms tool_calls=%d text_len=%s usage=%s",
            duration_ms,
            len(tool_calls),
            len(text) if text else 0,
            usage,
        )

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            duration_ms=duration_ms,
            raw=response,
        )


def _maybe_flag_runtime_failure(
    model: str, exc: BaseException, litellm_module: Any,
) -> None:
    """Translate a LiteLLM exception into a validator state update.

    Only permanent config problems (auth, model-not-found, bad-request —
    e.g. an unsupported/deprecated param) get propagated; the user needs
    to fix those. Transient errors (rate limit, connection blip) don't
    flip the UI to a failed state. Classification is the shared one in
    ``error_classification`` so every agent agrees on what's permanent.
    """
    from app.llm.error_classification import classify_llm_error

    kind = {
        "auth": "auth_failed",
        "not_found": "not_found",
        "bad_request": "bad_request",
    }.get(classify_llm_error(exc, litellm_module=litellm_module))
    if kind is None:
        return
    if "/" not in model:
        return
    provider = model.split("/", 1)[0].strip()
    if not provider:
        return
    try:
        from app.settings.validation import get_validator
        get_validator().mark_provider_failed(provider, kind, str(exc))
    except Exception:  # noqa: BLE001
        _log.exception("Failed to flag runtime LLM failure on validator")
