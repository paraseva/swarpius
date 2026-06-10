"""WebSocket broadcaster: AgentEvent → channel/payload emissions.

Subscribes to the AgentEvent bus produced by ``request_flow`` and maps
each event to its channel/payload shape for the frontend, including the
``coordinator_step`` "Thinking" transition events emitted for every step
transition the bus fires.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type

from app.constants import (
    CHANNEL_AGENT_OUTPUTS,
    CHANNEL_CHAT,
    CHANNEL_ERRORS,
    CHANNEL_LLM_DIAGNOSTICS,
    CHANNEL_TOOL_OUTPUTS,
    CHANNEL_USAGE_METRICS,
)
from app.coordinator.events import (
    ChatResponseEmitted,
    ConversationAssigned,
    DiagnosticClassificationCompleted,
    DiagnosticClassificationStarted,
    LlmCallCompleted,
    LlmCallFailed,
    LlmCallStarted,
    RateLimitDetected,
    RequestCompleted,
    RequestFailed,
    RequestStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    TtsSpeakRequested,
)
from app.coordinator.renderer import Renderer, ignore_event
from app.coordinator.trace import pretty_json as _pretty_json
from app.llm.rate_limit import emit_rate_limit_banner as _emit_rate_limit_banner


def _skill_label(tool_name: str) -> str:
    return " ".join(part.capitalize() for part in tool_name.split("_"))


class WsBroadcaster(Renderer):
    """Translate AgentEvent stream into WS channel + payload emissions.

    Stateful per-request: tracks whether the first ``LlmCallStarted``
    of this request has fired, so the ``call_started`` event is emitted
    once per request, not per step.
    """

    def __init__(
        self,
        ws_send_fn: Callable[..., None],
        runtime: Any,
    ) -> None:
        self._ws_send_fn = ws_send_fn
        self._runtime = runtime
        self._call_started_emitted: bool = False

    def _send(self, channel: str, payload: Any, meta: Optional[Dict] = None) -> None:
        # events.jsonl is written by RequestLogger, which subscribes to
        # the bus directly (works in every transport).
        self._ws_send_fn(channel, payload, meta=meta)

    def _handlers(self) -> Dict[Type[Any], Callable[[Any], None]]:
        return {
            RequestStarted: self._handle_request_started,
            ConversationAssigned: self._handle_conversation_assigned,
            DiagnosticClassificationStarted: self._handle_diagnostic_started,
            DiagnosticClassificationCompleted: self._handle_diagnostic_completed,
            LlmCallStarted: self._handle_llm_call_started,
            LlmCallCompleted: self._handle_llm_call_completed,
            LlmCallFailed: ignore_event,
            ToolStarted: self._handle_tool_started,
            ToolCompleted: self._handle_tool_completed,
            ToolFailed: self._handle_tool_failed,
            ChatResponseEmitted: self._handle_chat_response,
            TtsSpeakRequested: self._handle_tts_speak,
            RequestCompleted: self._handle_request_completed,
            RequestFailed: self._handle_request_failed,
            RateLimitDetected: ignore_event,
        }

    # ── Event handlers ────────────────────────────────────────────

    def _handle_request_started(self, event: RequestStarted) -> None:
        payload: Dict[str, Any] = {
            "event_type": "request_id_assignment",
            "source": "[Request]",
            "text": f"Request {event.request_id}: {event.user_input}",
            "user_input": event.user_input,
            "request_id": event.request_id,
            "coordinator_model": event.coordinator_model,
            "timestamp_ms": event.emitted_at_ms,
        }
        if event.client_msg_id is not None:
            payload["client_msg_id"] = event.client_msg_id
        self._send(CHANNEL_AGENT_OUTPUTS, payload)

    def _handle_conversation_assigned(self, event: ConversationAssigned) -> None:
        self._send(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "conversation_assigned",
            "request_id": event.request_id,
            "conversation_id": event.conversation_id,
            "topic_summary": event.topic_summary,
            "is_new": event.is_new,
            "timestamp_ms": event.emitted_at_ms,
        })

    def _handle_diagnostic_started(self, event: DiagnosticClassificationStarted) -> None:
        # The UI shows "Classifying..." on agent-outputs and a spinner
        # for the sub-LLM call on llm-diagnostics.
        self._send(CHANNEL_AGENT_OUTPUTS, {
            "event_type": "diagnostic_active",
            "source": "[Diagnostic]",
            "text": "Classifying conversation...",
            "timestamp_ms": event.emitted_at_ms,
        })
        self._send(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "call_started",
            "call_id": event.call_id,
            "agent_name": event.agent_name,
            "model": event.model,
            "timestamp_ms": event.emitted_at_ms,
        })

    def _handle_diagnostic_completed(self, event: DiagnosticClassificationCompleted) -> None:
        if event.success:
            self._send(CHANNEL_LLM_DIAGNOSTICS, {
                "event_type": "call_completed",
                "call_id": event.call_id,
            })
        else:
            self._send(CHANNEL_LLM_DIAGNOSTICS, {
                "event_type": "call_failed",
                "call_id": event.call_id,
                "error": event.error or "Diagnostic agent failed",
            })

    def _handle_llm_call_started(self, event: LlmCallStarted) -> None:
        if self._call_started_emitted:
            return
        self._call_started_emitted = True
        self._send(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "call_started",
            "call_id": event.request_id,
            "request_id": event.request_id,
            "agent_name": event.agent_name,
            "model": event.model,
            "prompt_tokens": event.prompt_tokens_estimated,
            "prompt_tokens_source": "estimated",
            "timestamp_ms": event.emitted_at_ms,
            "prompt_diagnostics": event.prompt_diagnostics,
        })

    def _handle_llm_call_completed(self, event: LlmCallCompleted) -> None:
        first_tool = event.selected_tools[0] if event.selected_tools else None
        display_label: Optional[str] = None
        if first_tool:
            reg = self._runtime.tool_registry.get(first_tool)
            display_label = reg.display_label if reg and reg.display_label else None
        self._send(CHANNEL_AGENT_OUTPUTS, {
            "source": "[Coordinator]",
            "event_type": "coordinator_step",
            "request_id": event.request_id,
            "step": event.step,
            "has_tool_calls": event.has_tool_calls,
            "has_text": event.has_text,
            "selected_skill": first_tool,
            "display_label": display_label,
            "done": event.is_terminal,
            "usage": event.usage,
            "timestamp_ms": event.emitted_at_ms,
        })

    def _handle_tool_started(self, event: ToolStarted) -> None:
        label = _skill_label(event.tool_name)
        self._send(CHANNEL_TOOL_OUTPUTS, {
            "agent_name": "Coordinator",
            "label": f"{label} Tool input",
            "source": f"[Coordinator: {label} Tool input]",
            "text": _pretty_json(event.args),
            "request_id": event.request_id,
        })
        self._send(CHANNEL_AGENT_OUTPUTS, {
            "source": "[Tool Call]",
            "event_type": "tool_call_started",
            "request_id": event.request_id,
            "step": event.step,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "display_label": event.display_label,
            "timestamp_ms": event.emitted_at_ms,
        })

    def _handle_tool_completed(self, event: ToolCompleted) -> None:
        label = _skill_label(event.tool_name)
        output_json = self._format_tool_output(event.tool_name, event.result)
        self._send(CHANNEL_TOOL_OUTPUTS, {
            "agent_name": "Coordinator",
            "label": f"{label} Tool output",
            "source": f"[Coordinator: {label} Tool output]",
            "text": output_json,
            "request_id": event.request_id,
        })
        self._send(CHANNEL_AGENT_OUTPUTS, {
            "source": "[Tool Call]",
            "event_type": "tool_call_completed",
            "request_id": event.request_id,
            "step": event.step,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "duration_ms": event.duration_ms,
            "timestamp_ms": event.emitted_at_ms,
        })

    def _handle_tool_failed(self, event: ToolFailed) -> None:
        label = _skill_label(event.tool_name)
        self._send(CHANNEL_TOOL_OUTPUTS, {
            "agent_name": "Coordinator",
            "label": f"{label} Tool output",
            "source": f"[Coordinator: {label} Tool output]",
            "text": _pretty_json({"error": event.error}),
            "request_id": event.request_id,
        })
        self._send(CHANNEL_AGENT_OUTPUTS, {
            "source": "[Tool Call]",
            "event_type": "tool_call_completed",
            "request_id": event.request_id,
            "step": event.step,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "duration_ms": event.duration_ms,
            "timestamp_ms": event.emitted_at_ms,
        })

    def _handle_chat_response(self, event: ChatResponseEmitted) -> None:
        from app.coordinator.sanitise import sanitise_for_tts as _sanitise_for_tts
        speak_text = _sanitise_for_tts(event.text) or ""
        self._send(
            CHANNEL_CHAT,
            {
                "agent_name": event.agent_name,
                "chat_response": event.text,
                "request_id": event.request_id,
            },
            meta={
                "agent_name": event.agent_name,
                "speak_text": speak_text,
                "request_id": event.request_id,
            },
        )

    def _handle_tts_speak(self, event: TtsSpeakRequested) -> None:
        if not event.text:
            return
        self._send(CHANNEL_AGENT_OUTPUTS, {
            "source": "[TTS]",
            "event_type": "tts_text",
            "request_id": event.request_id,
            "speak_text": event.text,
        })

    def _handle_request_completed(self, event: RequestCompleted) -> None:
        if event.status == "interrupted":
            self._send(CHANNEL_LLM_DIAGNOSTICS, {
                "event_type": "call_completed",
                "call_id": event.request_id,
                "request_id": event.request_id,
            })
            self._send(CHANNEL_AGENT_OUTPUTS, {
                "source": "[Request]",
                "event_type": "request_complete",
                "request_id": event.request_id,
                "status": "interrupted",
            })
            return

        self._send(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "call_completed",
            "call_id": event.request_id,
            "request_id": event.request_id,
            "agent_name": "Coordinator",
            "duration_ms": event.total_duration_ms,
            "timestamp_ms": event.emitted_at_ms,
        })
        if event.chat_response:
            self._send(CHANNEL_AGENT_OUTPUTS, {
                "source": "[Response]",
                "event_type": "response",
                "text": event.chat_response,
                "request_id": event.request_id,
            })

        from app.runtime.request_logger import extract_conversation_dir
        request_complete_payload: Dict[str, Any] = {
            "source": "[Request Complete]",
            "event_type": "request_complete",
            "request_id": event.request_id,
            "total_steps": event.total_steps,
            "total_duration_ms": event.total_duration_ms,
            "status": event.status,
            "usage": event.usage,
            "coordinator_model": event.coordinator_model,
            "conversation_id": extract_conversation_dir(event.request_id),
        }
        if event.topic_summary:
            request_complete_payload["topic_summary"] = event.topic_summary
        self._send(CHANNEL_AGENT_OUTPUTS, request_complete_payload)

        usage = event.usage or {}
        usage_payload = self._runtime.usage_tracker.record(
            agent_name="Coordinator",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
            cost_usd=usage.get("cost_usd"),
            source="provider",
        )
        self._send(CHANNEL_USAGE_METRICS, usage_payload)

    def _handle_request_failed(self, event: RequestFailed) -> None:
        self._send(CHANNEL_LLM_DIAGNOSTICS, {
            "event_type": "call_failed",
            "call_id": event.request_id,
            "request_id": event.request_id,
            "error": event.error,
        })
        if event.is_rate_limited:
            _emit_rate_limit_banner(
                self._ws_send_fn,
                "Coordinator",
                retriable=False,
                error_text=event.summary,
            )
        else:
            self._send(CHANNEL_ERRORS, {
                "source": "[Request]",
                "error": event.summary,
                "request_id": event.request_id,
            })

    # ── Helpers ──────────────────────────────────────────────────

    def _format_tool_output(self, tool_name: str, result: Any) -> str:
        from pydantic import BaseModel
        if isinstance(result, BaseModel):
            return self._runtime.tool_registry.compact_output(tool_name, result)
        if result is None:
            return "{}"
        return _pretty_json(result.model_dump(mode="json") if hasattr(result, "model_dump") else result)
