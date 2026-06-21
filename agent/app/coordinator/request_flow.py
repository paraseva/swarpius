"""Request processing flow using native tool-calling loop.

Each user request is processed by:
1. Assembling the system prompt (static + dynamic context sections)
2. Running the tool-calling loop (LLM decides tools vs text response)
3. Extracting chat_response + detailed_information from the result
4. Emitting via WebSocket and logging
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.runtime.request_logger import RequestLogger
    from app.runtime.state import RuntimeState

from app.constants import (
    CHANNEL_LLM_DIAGNOSTICS,
)
from app.coordinator.event_bus import EventBus
from app.coordinator.events import (
    ChatResponseEmitted,
    ConversationAssigned,
    DiagnosticClassificationCompleted,
    DiagnosticClassificationStarted,
    LlmCallCompleted,
    LlmCallStarted,
    RequestCompleted,
    RequestFailed,
    RequestStarted,
    ToolCompleted,
    ToolStarted,
    TtsSpeakRequested,
)
from app.coordinator.sanitise import sanitise_agent_chat_text as _sanitise_agent_chat_text
from app.coordinator.sanitise import sanitise_for_tts as _sanitise_for_tts
from app.coordinator.trace import build_trace_context as _build_trace_context
from app.exceptions import RequestInterrupted
from app.io.redact import redact_secrets as _redact_secrets
from app.llm.client import LLMResponse, is_known_llm_exception
from app.llm.diagnostic_agent import (
    ConversationAssignment,
    DiagnosticAgent,
    is_diagnostic_agent_enabled,
)
from app.llm.json_extract import extract_json_object
from app.llm.rate_limit import is_rate_limited_error as _is_rate_limited_error
from app.llm.tool_loop import run_tool_loop
from app.roon.tag_expansion import expand_list_tags as _expand_list_tags
from app.roon.tag_expansion import expand_queue_tags as _expand_queue_tags
from app.runtime.cancellation import raise_if_cancelled as _raise_if_cancelled
from app.runtime.request_context import clear_request_id, set_request_id
from app.runtime.request_logger import RequestIdGenerator, RequestLogger
from app.schemas import InterruptArbiterOutputSchema

_log = logging.getLogger("swarpius.request_flow")

# ── Prompt caching ──────────────────────────────────────────────────

def is_prompt_caching_enabled() -> bool:
    """Whether cache_control markers should be added to system messages
    and tool definitions. Resolved through the locked-at-startup
    settings cache (see ``app.settings``)."""
    from app.settings import get_settings
    return get_settings().enable_prompt_caching

# Provider prefixes that accept inline cache_control markers. Anthropic
# uses them natively; LiteLLM auto-translates the same syntax into
# Google's cachedContents API for Gemini. OpenAI and DeepSeek cache
# automatically and don't take markers, so they're excluded here even
# though LiteLLM's supports_prompt_caching() would say True for them.
_MARKER_PROVIDER_PREFIXES: tuple[str, ...] = (
    "anthropic/",
    "gemini/",
    "vertex_ai/",
)


def _supports_cache_markers(model: Optional[str]) -> bool:
    """Return True when the given model accepts cache_control markers."""
    if not model:
        return False
    return model.startswith(_MARKER_PROVIDER_PREFIXES)


def _format_system_message(
    content: str,
    cache_enabled: bool,
    dynamic_tail: Optional[str] = None,
) -> Dict[str, Any]:
    """Format the system message, optionally with cache_control markers.

    When ``dynamic_tail`` is provided and non-empty, the message is split
    into two content blocks so cache_control can land at the static /
    dynamic boundary. That gives explicit-marker providers (Anthropic,
    Gemini via LiteLLM) a cross-request cache on the stable prefix plus
    the existing intra-request cache on the whole prompt. See
    TO_DO/improved-multiprovider-caching.md.
    """
    if not cache_enabled:
        combined = content + (dynamic_tail or "")
        return {"role": "system", "content": combined}

    blocks: List[Dict[str, Any]] = [
        {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}},
    ]
    if dynamic_tail:
        blocks.append(
            {"type": "text", "text": dynamic_tail, "cache_control": {"type": "ephemeral"}},
        )
    return {"role": "system", "content": blocks}


def _apply_tool_cache_control(tools: List[dict]) -> None:
    """Add cache_control to the last tool definition for provider-side caching."""
    if tools:
        tools[-1]["function"]["cache_control"] = {"type": "ephemeral"}


def _write_context_snapshot(
    logger: "RequestLogger",
    runtime: "RuntimeState",
    coordinator_model: Optional[str],
) -> None:
    """Capture non-secret coordinator config at the start of a conversation.

    Idempotent at the conversation (``cXX``) directory: only the first
    request of each conversation writes the file.  The passive analyser
    reads this so its findings reflect the persona / zone / model /
    registered skills as they were when the conversation ran, not as
    they are when analysis runs.
    """
    from app.settings import get_settings
    settings = get_settings()
    persona = settings.llm_persona
    default_zone = (
        runtime.roon_connection.get_default_zone()
        if runtime.roon_connection else None
    )

    resolved = runtime.resolved_profile
    model_profile: Dict[str, Any] = {}
    if resolved is not None:
        model_profile = {
            "max_coordinator_steps": resolved.model_profile.max_coordinator_steps,
            "soft_nudge_step": resolved.model_profile.soft_nudge_step,
            "temperature": resolved.temperature,
            "top_p": resolved.top_p,
        }
        if resolved.matched_pattern:
            model_profile["matched_pattern"] = resolved.matched_pattern

    skills = [
        {"name": s.metadata.name, "description": s.metadata.description}
        for s in (runtime.agent_skills or [])
    ]

    try:
        logger.write_context_snapshot({
            "persona": persona,
            "default_zone": default_zone,
            "coordinator_model": coordinator_model,
            "model_profile": model_profile,
            "registered_skills": skills,
        })
    except Exception:  # noqa: BLE001 — never let snapshot write crash the request
        _log.warning("Failed to write context snapshot", exc_info=True)


def _log_llm_failure(
    log: logging.Logger,
    source: str,
    err: BaseException,
    *,
    fatal: bool,
) -> str:
    """Log an LLM-call failure consistently across agents: known provider
    errors / timeouts log concise (no traceback), the unexpected get the
    full trace. ``fatal`` sets the level for known errors — ERROR when the
    request dies, WARNING when the agent degrades and carries on. ``source``
    names the agent/call. Returns the redacted message for surfacing.
    """
    err_text = (_redact_secrets(str(err)) or "").strip()
    if is_known_llm_exception(err):
        log.log(
            logging.ERROR if fatal else logging.WARNING,
            "%s failed: %s: %s", source, type(err).__name__, err_text,
        )
    else:
        log.error(
            "%s failed unexpectedly: %s", source, type(err).__name__, exc_info=True,
        )
    return err_text


def _summarise_tool_loop_error(err: Exception, err_text: str, coordinator_model: str) -> str:
    """Build a user-facing summary of a tool-loop exception.

    Takes the first line of the exception message (strips multiline stack
    traces) and prefixes it with the exception class. Full exception
    details also go to the server log and the llm-diagnostics channel.
    """
    if _is_rate_limited_error(err_text):
        return (
            f"Rate limit exceeded for {coordinator_model}. "
            "The provider has throttled our requests — please try again shortly."
        )
    first_line = err_text.split("\n", 1)[0] if err_text else ""
    label = type(err).__name__
    return f"{label}: {first_line}" if first_line else label


def _step_trace(
    step: int,
    global_step: int,
    selected_skill: Optional[str],
    tool_params: Optional[BaseModel],
    tool_output: Optional[BaseModel],
    runtime_state: Any,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    from datetime import datetime

    from app.coordinator.trace import compact_for_context as _compact_for_context

    compact_params = (
        _compact_for_context(tool_params.model_dump(mode="json"), runtime_state)
        if tool_params
        else None
    )
    compact_output = None
    if tool_output:
        registry = getattr(runtime_state, "tool_registry", None)
        if registry and selected_skill:
            compact_output = registry.compact_trace(selected_skill, tool_output)
        if compact_output is None:
            compact_output = _compact_for_context(
                tool_output.model_dump(mode="json"), runtime_state,
            )
    return {
        "step": step,
        "global_step": global_step,
        "selected_skill": selected_skill,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tool_parameters": compact_params,
        "tool_output": compact_output,
        "note": note,
    }


# ── Prompt assembly ──────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return max(1, len(text) // 4)


# Titles of context sections that belong in the cacheable static prefix.
# Everything else is dynamic tail and must not be covered by the static
# cache marker. See TO_DO/improved-multiprovider-caching.md.
_STATIC_SECTION_TITLES = frozenset({
    "Skill Definitions",
    "Zone Aliases",
    "Current Date",
})


def _build_system_message(runtime: Any) -> tuple[str, str]:
    """Assemble the system prompt split into (static_prefix, dynamic_tail).

    The boundary lets the caller place cache_control at the end of the
    static prefix so providers with explicit markers (Anthropic, Gemini
    via LiteLLM) get a cross-request cache hit on the stable portion.
    Concatenating the two strings yields the complete original prompt.
    """
    runtime.set_prompt_state_context()

    static_parts: List[str] = [runtime.coordinator_system_prompt]
    dynamic_parts: List[str] = []

    for section in runtime.get_context_sections():
        rendered = f"\n## {section['title']}\n{section['content']}"
        if section["title"] in _STATIC_SECTION_TITLES:
            static_parts.append(rendered)
        else:
            dynamic_parts.append(rendered)

    static_prefix = "\n".join(static_parts)
    dynamic_tail = "\n".join(dynamic_parts)
    # Ensure a separator between the two halves when both are non-empty
    if dynamic_tail:
        dynamic_tail = "\n" + dynamic_tail
    return static_prefix, dynamic_tail


def _collect_prompt_diagnostics(
    system_content: str,
    runtime: Any,
) -> Dict[str, Any]:
    """Compute prompt diagnostics for the call_started event."""
    base_tokens = _estimate_tokens(runtime.coordinator_system_prompt)
    context_breakdown = []
    context_total = 0
    for section in runtime.get_context_sections():
        section_tokens = _estimate_tokens(section["content"])
        context_breakdown.append({
            "name": section["title"],
            "estimated_tokens": section_tokens,
            "char_count": len(section["content"]),
        })
        context_total += section_tokens

    schema_json = json.dumps(runtime.tool_registry.to_tool_schemas())
    schema_tokens = _estimate_tokens(schema_json)
    total = base_tokens + context_total + schema_tokens

    return {
        "estimated_input_tokens": total,
        "system_prompt_tokens_estimated": base_tokens,
        "context_tokens_estimated": context_total,
        "input_schema_tokens_estimated": schema_tokens,
        "context_breakdown": context_breakdown,
    }


# ── Interrupt arbitration ────────────────────────────────────────


def is_interrupt_arbiter_enabled() -> bool:
    """Whether the LLM-backed interrupt arbiter runs. Read at call time
    so tests can flip it without touching import-time state."""
    from app.settings import get_settings
    return get_settings().enable_interrupt_arbiter


def arbitrate_interrupt(
    runtime: Any,
    active_request: str,
    incoming_request: str,
    ws_send_fn: Optional[Callable[[str, Any], None]] = None,
) -> InterruptArbiterOutputSchema:
    """Decide whether a new message should interrupt the active request.

    Runs a lightweight LLM call via the arbiter client to choose
    between queue / interrupt_and_replace / interrupt_only. Explicit
    stop/cancel keyword bodies are intercepted earlier by
    :func:`app.io.websocket_flow._handle_keyword_directive` and never
    reach here. ``ENABLE_INTERRUPT_ARBITER=false`` skips the LLM and
    queues.
    """
    runtime.ensure_initialised()

    if not is_interrupt_arbiter_enabled():
        return InterruptArbiterOutputSchema(
            action="queue",
            reason="Interrupt arbiter disabled (ENABLE_INTERRUPT_ARBITER=false)",
            confidence=0.0,
        )

    if not runtime.arbiter_client:
        return InterruptArbiterOutputSchema(
            action="queue",
            reason="Interrupt arbiter unavailable",
            confidence=0.0,
        )

    arbiter_call_id = f"arb-{uuid.uuid4().hex[:12]}"
    if ws_send_fn is not None:
        ws_send_fn(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "call_started",
            "call_id": arbiter_call_id,
            "agent_name": "Arbiter",
            "model": runtime.arbiter_client.model,
            "timestamp_ms": int(time.time() * 1000),
        })

    error_text: Optional[str] = None
    try:
        from app.runtime.state import RuntimeState
        messages = [
            {"role": "system", "content": RuntimeState.build_arbiter_system_prompt()},
            {"role": "user", "content": json.dumps({
                "active_request": active_request,
                "incoming_request": incoming_request,
            })},
        ]
        response = asyncio.run(runtime.arbiter_client.completion(
            messages=messages, tools=None,
        ))
        if response.text:
            try:
                parsed = extract_json_object(response.text)
                decision = InterruptArbiterOutputSchema(**parsed)
                if ws_send_fn is not None:
                    ws_send_fn(CHANNEL_LLM_DIAGNOSTICS, {
                        "event_type": "call_completed",
                        "call_id": arbiter_call_id,
                    })
                _log.info(
                    "Arbiter decision: %s (confidence %.2f) — %s",
                    decision.action, decision.confidence, decision.reason,
                )
                return decision
            except Exception as parse_exc:
                error_text = f"{type(parse_exc).__name__}: response not parseable as InterruptArbiterOutputSchema"
                _log.warning(
                    "Arbiter response could not be parsed (%s): %r",
                    type(parse_exc).__name__, response.text,
                )
        else:
            error_text = "Arbiter returned no text content"
            _log.warning("Arbiter returned no text content; defaulting to queue")
    except Exception as err:
        _log_llm_failure(_log, "Arbiter (interrupt decision)", err, fatal=False)
        error_text = f"{type(err).__name__}: {err}"
    if ws_send_fn is not None:
        ws_send_fn(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "call_failed",
            "call_id": arbiter_call_id,
            "error": error_text or "Arbiter failed",
        })

    return InterruptArbiterOutputSchema(
        action="queue",
        reason="Arbiter failed; defaulting to queue",
        confidence=0.0,
    )


def _run_diagnostic_classification(
    gen: "RequestIdGenerator",
    runtime: Any,
    user_input: str,
    bus: EventBus,
) -> Optional[ConversationAssignment]:
    """Run the optional diagnostic-agent classification step.

    Returns ``None`` when:
      - ``runtime.diagnostic_client`` is not configured, or
      - ``ENABLE_DIAGNOSTIC_AGENT`` is false (checked inside the agent), or
      - The classification call times out (5s) or raises.

    Emits ``DiagnosticClassificationStarted`` / ``Completed`` on the
    bus. Applying the assignment mutates ``gen.tracker`` — the caller
    relies on that side effect to mint/pick the conversation ID the
    request logs into.
    """
    if not runtime.diagnostic_client:
        return None

    diagnostic_agent = DiagnosticAgent(runtime.diagnostic_client, gen.tracker)
    if not is_diagnostic_agent_enabled():
        return None

    diag_call_id = f"diag-{uuid.uuid4().hex[:12]}"
    bus.emit(DiagnosticClassificationStarted(
        request_id=None,
        emitted_at_ms=int(time.time() * 1000),
        call_id=diag_call_id,
        agent_name="Diagnostic",
        model=runtime.diagnostic_client.model,
    ))

    assignment: Optional[ConversationAssignment] = None
    success = False
    error_text: Optional[str] = None
    try:
        assignment = asyncio.run(
            asyncio.wait_for(diagnostic_agent.assign_conversation(user_input), timeout=5.0),
        )
        if assignment:
            diagnostic_agent.apply_assignment(assignment)
            _log.info(
                "Diagnostic classification: %s (%s) — %s",
                assignment.conversation_id,
                "new" if assignment.is_new else "continue",
                assignment.topic_summary,
            )
            success = True
        else:
            error_text = "Diagnostic agent returned no usable assignment"
    except Exception as exc:
        _log_llm_failure(
            _log, "Diagnostic agent (classification) — falling back to timeout",
            exc, fatal=False,
        )
        error_text = f"{type(exc).__name__}: {exc}"
    finally:
        bus.emit(DiagnosticClassificationCompleted(
            request_id=None,
            emitted_at_ms=int(time.time() * 1000),
            call_id=diag_call_id,
            conversation_id=assignment.conversation_id if assignment else None,
            topic_summary=assignment.topic_summary if assignment else None,
            is_new=assignment.is_new if assignment else None,
            success=success,
            error=None if success else error_text,
        ))

    return assignment


class _CoordinatorObserver:
    """Bundles the three observability callbacks the tool loop calls
    at well-known points (tool start/end and after each LLM response)
    so process_request doesn't have to define 160 lines of nested
    closures inline."""

    def __init__(
        self,
        runtime: Any,
        logger: "RequestLogger",
        request_id: str,
        messages: List[Dict[str, Any]],
        bus: EventBus,
        prompt_diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.runtime = runtime
        self.logger = logger
        self.request_id = request_id
        self.messages = messages
        self.bus = bus
        self._prompt_diagnostics = prompt_diagnostics or {}

    @staticmethod
    def _skill_label(tool_name: str) -> str:
        return " ".join(part.capitalize() for part in tool_name.split("_"))

    def _display_label_for(self, tool_name: str) -> str:
        reg = self.runtime.tool_registry.get(tool_name)
        if reg and reg.display_label:
            return reg.display_label
        return self._skill_label(tool_name)

    def on_llm_request_start(self, step: int) -> None:
        # Step 1's event carries prompt_diagnostics for the legacy
        # ``call_started`` WS emission. Subsequent steps don't (the
        # diagnostics are request-level, not per-step).
        diag = self._prompt_diagnostics if step == 1 else {}
        self.bus.emit(LlmCallStarted(
            request_id=self.request_id,
            emitted_at_ms=int(time.time() * 1000),
            call_id=f"{self.request_id}-step{step}",
            step=step,
            agent_name="Coordinator",
            model=getattr(self.runtime.llm_client, "model", None),
            prompt_tokens_estimated=diag.get("estimated_input_tokens", 0),
            prompt_diagnostics=diag,
        ))

    def on_tool_start(self, tool_call_id: str, tool_name: str, args: dict, step: int) -> None:
        display = self._display_label_for(tool_name)
        self.bus.emit(ToolStarted(
            request_id=self.request_id,
            emitted_at_ms=int(time.time() * 1000),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            step=step,
            args=args,
            display_label=display,
        ))

    def on_tool_end(
        self,
        tool_call_id: str,
        tool_name: str,
        args: dict,
        result: Any,
        step: int,
        duration_ms: int,
        error: Optional[str] = None,
    ) -> None:
        self.bus.emit(ToolCompleted(
            request_id=self.request_id,
            emitted_at_ms=int(time.time() * 1000),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            step=step,
            result=result,
            duration_ms=duration_ms,
        ))

        parsed_input = None
        if result and isinstance(result, BaseModel):
            try:
                reg_tool = self.runtime.tool_registry.get(tool_name)
                if reg_tool:
                    parsed_input = reg_tool.input_schema.model_validate(args)
            except Exception:
                # parsed_input feeds only trace/logging — if the LLM
                # sent args that fail validation, leave parsed_input
                # None and proceed; the tool layer reports the real
                # error to the model.
                pass

        self.runtime.global_step += 1
        trace_entry = _step_trace(
            step=step,
            global_step=self.runtime.global_step,
            selected_skill=tool_name,
            tool_params=parsed_input,
            tool_output=result if isinstance(result, BaseModel) else None,
            runtime_state=self.runtime,
        )
        self.runtime.execution_trace.append(trace_entry)
        from app.settings import get_settings as _gs
        _trace_max = _gs().execution_trace_max_length
        if len(self.runtime.execution_trace) > _trace_max:
            self.runtime.execution_trace[:] = self.runtime.execution_trace[-_trace_max:]
        self.runtime.execution_trace_provider.set_context(
            _build_trace_context(
                self.runtime.execution_trace,
                current_global_step=self.runtime.global_step,
            ),
        )

        # search_attempts / search_retry_notes are only set by roon_search
        # (propagated up from RoonCoreResultsSchema).
        result_data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
        if tool_name == "roon_search":
            attempt = getattr(result, "search_attempts", 1) or 1
            retry_notes = getattr(result, "search_retry_notes", None)
        else:
            attempt = 1
            retry_notes = None
        self.logger.log_tool_execution(
            step=step,
            selected_skill=tool_name,
            tool_input=args,
            tool_output=result_data,
            duration_ms=duration_ms,
            attempt=attempt,
            retry_notes=retry_notes,
            error=error,
        )

    def on_llm_response(self, response: LLMResponse, step: int) -> None:
        if response.has_tool_calls:
            coordinator_output = {
                "action": "tool_call",
                "tool_calls": [
                    {"tool": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
        else:
            coordinator_output = {
                "action": "text_response",
                "text": response.text,
            }

        self.bus.emit(LlmCallCompleted(
            request_id=self.request_id,
            emitted_at_ms=int(time.time() * 1000),
            call_id=f"{self.request_id}-step{step}",
            step=step,
            duration_ms=response.duration_ms or 0,
            usage=response.usage,
            has_tool_calls=response.has_tool_calls,
            has_text=bool(response.text),
            selected_tools=tuple(tc.name for tc in response.tool_calls),
            is_terminal=not response.has_tool_calls,
        ))

        # Skip messages[0] — system message is logged separately.
        conversation_messages = self.messages[1:] if len(self.messages) > 1 else []

        self.logger.log_coordinator_step(
            step=step,
            coordinator_input=conversation_messages,
            coordinator_output=coordinator_output,
            context_snapshot=None,
            duration_ms=response.duration_ms,
            usage=response.usage or None,
        )


def _persist_user_chat(user_input: str, client_msg_id: Optional[str]) -> None:
    """Persist the user's message as the first transcript entry of a request,
    at a non-restart terminal — grouped with the request rather than written
    on receipt, so a restart that drops the in-flight request leaves no
    orphaned message. Skipped when a restart has been requested (the in-flight
    request is dropped). Direct append (not via the bus) so it is stored for
    replay without being re-sent to the client, which already shows its own
    message. ``direction='outbound'`` is the frontend's client-centric
    convention (a user bubble) — see WebSocketProvider.tsx / ChatPanel.tsx.
    """
    from app.io.message_store import get_message_store
    from app.runtime.restart_signal import is_restart_requested
    if is_restart_requested():
        return
    meta: dict = {"direction": "outbound"}
    if client_msg_id is not None:
        meta["client_msg_id"] = client_msg_id
    get_message_store().append("chat", {"channel": "chat", "body": user_input}, meta=meta)


def _handle_loop_exception(
    err: Exception,
    *,
    coordinator_model: Optional[str],
    request_id: str,
    bus: EventBus,
    logger: "RequestLogger",
) -> None:
    """Handle a tool-loop failure: log the failure, emit a
    ``RequestFailed`` event for adapters to surface, and record the
    outcome."""
    err_text = _log_llm_failure(_log, "Coordinator tool loop", err, fatal=True)
    summary = _summarise_tool_loop_error(err, err_text, coordinator_model)
    bus.emit(RequestFailed(
        request_id=request_id,
        emitted_at_ms=int(time.time() * 1000),
        error=err_text,
        summary=summary,
        coordinator_model=coordinator_model,
        is_rate_limited=_is_rate_limited_error(err_text),
    ))
    logger.log_outcome(
        status="error",
        chat_response=summary,
        total_steps=0,
        coordinator_model=coordinator_model,
    )


# ── Main request handler ─────────────────────────────────────────


def process_request(
    runtime: Any,
    user_input: str,
    cancel_event: Optional[threading.Event],
    event_bus: EventBus,
    request_id_generator: Optional[RequestIdGenerator] = None,
    request_logger: Optional[RequestLogger] = None,
    run_mode_label: str = "cli",
    client_msg_id: Optional[str] = None,
) -> None:
    """Process a single user request through the native tool-calling loop.

    The flow emits typed ``AgentEvent`` instances on ``event_bus`` at
    every lifecycle transition. Transport adapters (CLI renderer, WS
    broadcaster) subscribe to the bus and translate events into their
    own surface — no transport-specific branching lives in here.

    Callers MUST subscribe whatever adapters they need before calling.
    ``run_mode_label`` is informational only: it's included on
    ``RequestStarted`` and in ``log_request`` so the analyser and the
    chronological log can tell CLI runs apart from WS ones.
    """
    bus = event_bus

    runtime.ensure_initialised()

    roon_action = runtime.tool_registry.get("roon_action")
    if roon_action and roon_action.tool_instance:
        roon_action.tool_instance.cancel_event = cancel_event
    coordinator_model = runtime.llm_client.model if runtime.llm_client else None

    # ── Diagnostic agent + Request ID setup ──
    gen: Optional[RequestIdGenerator] = None
    assignment = None  # ConversationAssignment from diagnostic agent
    if request_logger is not None:
        logger: RequestLogger = request_logger
    else:
        gen = request_id_generator or RequestIdGenerator()

        # Run diagnostic agent BEFORE minting request ID so the cXX
        # in the ID and log directory reflects the semantic classification.
        assignment = _run_diagnostic_classification(
            gen, runtime, user_input, bus,
        )

        request_id = gen.next_id()
        logger = RequestLogger(request_id)
    request_id = logger.request_id
    set_request_id(request_id)
    from app.runtime.server_logger import get_server_logger as _get_slog
    _get_slog().set_request_id(request_id)
    logger.log_request(user_input=user_input, run_mode=run_mode_label)
    _write_context_snapshot(logger, runtime, coordinator_model)

    # The logger is a peer subscriber to the bus — writes events.jsonl
    # in every transport, not just WS.
    bus.subscribe(logger.handle)

    request_start_ms = int(time.time() * 1000)

    # ── Assemble messages for the tool-calling loop ──
    static_prefix, dynamic_tail = _build_system_message(runtime)
    system_content = static_prefix + dynamic_tail

    # Prompt caching: only apply for providers that accept inline markers
    cache_this_request = (
        is_prompt_caching_enabled()
        and _supports_cache_markers(getattr(runtime.llm_client, "model", None))
    )

    messages: List[Dict[str, Any]] = [
        _format_system_message(
            static_prefix,
            cache_enabled=cache_this_request,
            dynamic_tail=dynamic_tail,
        ),
        {"role": "user", "content": user_input},
    ]

    tools = runtime.tool_registry.to_tool_schemas()
    if cache_this_request:
        _apply_tool_cache_control(tools)

    logger.log_prompt_snapshot(
        agent_name="Coordinator",
        system_prompt=system_content,
    )

    prompt_diag = _collect_prompt_diagnostics(system_content, runtime)
    observer = _CoordinatorObserver(
        runtime=runtime,
        logger=logger,
        request_id=request_id,
        messages=messages,
        bus=bus,
        prompt_diagnostics=prompt_diag,
    )

    bus.emit(RequestStarted(
        request_id=request_id,
        emitted_at_ms=int(time.time() * 1000),
        user_input=user_input,
        coordinator_model=coordinator_model,
        run_mode_label=run_mode_label,
        client_msg_id=client_msg_id,
    ))
    if assignment:
        bus.emit(ConversationAssigned(
            request_id=request_id,
            emitted_at_ms=int(time.time() * 1000),
            conversation_id=assignment.conversation_id,
            topic_summary=assignment.topic_summary,
            is_new=assignment.is_new,
        ))

    try:
        _raise_if_cancelled(cancel_event, "before tool loop")

        async def _run_request_async() -> Any:
            loop_kwargs: dict = {}
            if runtime.model_profile:
                loop_kwargs["hard_limit"] = runtime.model_profile.max_coordinator_steps
                loop_kwargs["soft_nudge_step"] = runtime.model_profile.soft_nudge_step
            return await run_tool_loop(
                client=runtime.llm_client,
                registry=runtime.tool_registry,
                messages=messages,
                tools=tools,
                on_tool_start=observer.on_tool_start,
                on_tool_end=observer.on_tool_end,
                on_llm_request_start=observer.on_llm_request_start,
                on_llm_response=observer.on_llm_response,
                on_store_results=runtime.store_result_entries,
                cancel_event=cancel_event,
                **loop_kwargs,
            )

        loop_result = asyncio.run(_run_request_async())
    except RequestInterrupted:
        logger.log_outcome(status="interrupted", total_steps=0, coordinator_model=coordinator_model)
        logger.update_conversation_summary(
            topic_summary=assignment.topic_summary if assignment else None,
        )
        bus.emit(RequestCompleted(
            request_id=request_id,
            emitted_at_ms=int(time.time() * 1000),
            status="interrupted",
            chat_response="",
            total_duration_ms=int(time.time() * 1000) - request_start_ms,
            total_steps=0,
            usage=None,
            coordinator_model=coordinator_model,
        ))
        _persist_user_chat(user_input, client_msg_id)
        runtime.persist_state()
        return
    except Exception as err:
        _handle_loop_exception(
            err,
            coordinator_model=coordinator_model,
            request_id=request_id,
            bus=bus,
            logger=logger,
        )
        logger.update_conversation_summary(
            topic_summary=assignment.topic_summary if assignment else None,
        )
        _persist_user_chat(user_input, client_msg_id)
        runtime.persist_state()
        return

    # Persist the user's message first (ordered before the response, which
    # the bus appends below) now that the request has reached a terminal.
    _persist_user_chat(user_input, client_msg_id)

    # ── Extract and emit the response ──
    raw_text = loop_result.text or ""

    raw_text = _expand_list_tags(raw_text, runtime.result_store)
    raw_text = _expand_queue_tags(
        raw_text, runtime.queue_display_cache, runtime.resolve_zone_name,
    )

    # Sanitise for chat leaks but preserve <extended_info> markup
    chat_response = _sanitise_agent_chat_text(raw_text) or ""
    total_steps = loop_result.steps
    now_ms = int(time.time() * 1000)
    total_duration_ms = now_ms - request_start_ms
    usage = loop_result.total_usage

    if chat_response:
        bus.emit(ChatResponseEmitted(
            request_id=request_id,
            emitted_at_ms=now_ms,
            text=chat_response,
            agent_name="Coordinator",
        ))
        speak_text = _sanitise_for_tts(chat_response) or ""
        if speak_text:
            bus.emit(TtsSpeakRequested(
                request_id=request_id,
                emitted_at_ms=now_ms,
                text=speak_text,
            ))

    runtime.conversation_history_provider.add_turn(user_input, chat_response)

    if gen and chat_response:
        from app.llm.diagnostic_agent import truncate_response
        gen.tracker.set_last_response(gen.conversation_id, truncate_response(chat_response))

    status = "completed" if loop_result.terminated_by == "completion" else loop_result.terminated_by
    logger.log_outcome(
        status=status,
        chat_response=chat_response,
        total_steps=total_steps,
        topic_summary=assignment.topic_summary if assignment else None,
        assignment_source="diagnostic_agent" if assignment else None,
        coordinator_model=coordinator_model,
        usage=usage or None,
    )
    logger.update_conversation_summary(
        topic_summary=assignment.topic_summary if assignment else None,
    )

    bus.emit(RequestCompleted(
        request_id=request_id,
        emitted_at_ms=int(time.time() * 1000),
        status=status,
        chat_response=chat_response,
        total_duration_ms=total_duration_ms,
        total_steps=total_steps,
        usage=usage,
        coordinator_model=coordinator_model,
        topic_summary=assignment.topic_summary if assignment else None,
    ))

    _log.info(
        "Coordinator request %s %s: %d step(s), %dms",
        request_id, status, total_steps, total_duration_ms,
    )

    runtime.persist_state()
    clear_request_id()
