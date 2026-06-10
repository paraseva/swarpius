"""AgentEvent stream emitted by request_flow.

These events describe the agent's lifecycle: when LLM calls start and
finish, when tools start and complete, when responses go out. Adapters
(CLI renderer, WS broadcaster, RequestLogger) subscribe to the bus and
translate each event into their own surface.

The event taxonomy IS the contract between the core request flow and
every transport. Adding a new transport is just subscribing to these
events and rendering them.

All events are frozen dataclasses; producers fill every field at
construction time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union


@dataclass(frozen=True, slots=True)
class RequestStarted:
    request_id: str
    emitted_at_ms: int
    user_input: str
    coordinator_model: Optional[str]
    run_mode_label: str
    # Opaque id the frontend sends on the chat frame; echoed on
    # ``request_id_assignment`` so the FE can pair badges by lookup.
    # None for CLI or any caller that doesn't supply one.
    client_msg_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RequestCompleted:
    request_id: str
    emitted_at_ms: int
    status: str
    chat_response: str
    total_duration_ms: int
    total_steps: int
    usage: Optional[dict]
    coordinator_model: Optional[str]
    topic_summary: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RequestFailed:
    request_id: str
    emitted_at_ms: int
    error: str
    summary: str
    coordinator_model: Optional[str]
    is_rate_limited: bool


@dataclass(frozen=True, slots=True)
class ConversationAssigned:
    request_id: str
    emitted_at_ms: int
    conversation_id: str
    topic_summary: Optional[str]
    is_new: bool


@dataclass(frozen=True, slots=True)
class DiagnosticClassificationStarted:
    # request_id may be None — diagnostic classification runs BEFORE
    # the request ID is minted (the classification determines the
    # conversation, which determines the cXX in the ID).
    request_id: Optional[str]
    emitted_at_ms: int
    call_id: str
    agent_name: str
    model: Optional[str]


@dataclass(frozen=True, slots=True)
class DiagnosticClassificationCompleted:
    request_id: Optional[str]
    emitted_at_ms: int
    call_id: str
    conversation_id: Optional[str]
    topic_summary: Optional[str]
    is_new: Optional[bool]
    success: bool
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class LlmCallStarted:
    request_id: str
    emitted_at_ms: int
    call_id: str
    step: int
    agent_name: str
    model: Optional[str]
    prompt_tokens_estimated: int
    prompt_diagnostics: dict


@dataclass(frozen=True, slots=True)
class LlmCallCompleted:
    request_id: str
    emitted_at_ms: int
    call_id: str
    step: int
    duration_ms: int
    usage: Optional[dict]
    has_tool_calls: bool
    has_text: bool
    selected_tools: tuple
    is_terminal: bool


@dataclass(frozen=True, slots=True)
class LlmCallFailed:
    request_id: str
    emitted_at_ms: int
    call_id: str
    step: int
    error: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ToolStarted:
    request_id: str
    emitted_at_ms: int
    tool_call_id: str
    tool_name: str
    step: int
    args: dict
    display_label: str


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    request_id: str
    emitted_at_ms: int
    tool_call_id: str
    tool_name: str
    step: int
    result: Any
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ToolFailed:
    request_id: str
    emitted_at_ms: int
    tool_call_id: str
    tool_name: str
    step: int
    error: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ChatResponseEmitted:
    request_id: str
    emitted_at_ms: int
    text: str
    agent_name: str


@dataclass(frozen=True, slots=True)
class TtsSpeakRequested:
    request_id: str
    emitted_at_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class RateLimitDetected:
    request_id: Optional[str]
    emitted_at_ms: int
    provider: str
    retry_in_seconds: Optional[int]
    message: str
    is_retriable: bool


AgentEvent = Union[
    RequestStarted,
    RequestCompleted,
    RequestFailed,
    ConversationAssigned,
    DiagnosticClassificationStarted,
    DiagnosticClassificationCompleted,
    LlmCallStarted,
    LlmCallCompleted,
    LlmCallFailed,
    ToolStarted,
    ToolCompleted,
    ToolFailed,
    ChatResponseEmitted,
    TtsSpeakRequested,
    RateLimitDetected,
]
