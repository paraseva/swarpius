"""CLI renderer: AgentEvent → Rich panels + Live(Group) spinner.

Subscribes to the AgentEvent bus and translates events into:

- Rich ``Panel``s for user-input and Swarpius-response messages
- A ``Live(Group(...))`` of spinners that renders N concurrent rows
  for parallel tools, falling back to a single "Thinking..." spinner
  when no tools are active
- Direct ``tts_say_fn`` calls
- The ``on_request_complete`` session-usage callback

The Live display is owned by the renderer. Use as a context manager
so the spinner cleans up on exit:

    cli_renderer = CliRenderer(rich_console=console, ...)
    event_bus.subscribe(cli_renderer.handle)
    with cli_renderer:
        process_request(..., event_bus=event_bus)

When the renderer is used outside a ``with`` block (e.g. inside tests
or when ``process_request`` auto-wires it), state updates still happen
correctly but no Live display is drawn — ``active_row_labels()``
reflects what would be rendered.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Type

from rich.panel import Panel

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

_THINKING_LABEL = "Thinking"
_CLASSIFYING_LABEL = "Classifying"


class CliRenderer(Renderer):
    def __init__(
        self,
        rich_console: Any,
        tts_say_fn: Optional[Callable[[str, str], None]] = None,
        on_request_complete: Optional[Callable[[dict, int, int], None]] = None,
        show_request_ids: bool = False,
    ) -> None:
        self._console = rich_console
        self._tts_say_fn = tts_say_fn
        self._on_request_complete = on_request_complete
        self._show_request_ids = show_request_ids

        # Active tool rows, ordered by dispatch (Python dicts preserve
        # insertion order — that's the on-screen order).
        self._active_tools: Dict[str, str] = {}
        self._thinking_active: bool = False
        self._classifying_active: bool = False
        self._live: Optional[Any] = None

    # ── Live display lifecycle ──────────────────────────────────

    def __enter__(self) -> "CliRenderer":
        from rich.live import Live
        self._live = Live(
            self._render_group(),
            console=self._console,
            # Match the smoothness of ``console.status`` (default
            # 12.5 fps). Below that the dots-spinner visibly stutters.
            refresh_per_second=12.5,
            # transient=False: leave the final rendered frame in place
            # on exit. We clear the group's contents (no spinners,
            # no thinking row) before stopping Live, so the "final
            # frame" is empty — no trailing-newline artifact between
            # subsequent prints.
            transient=False,
        )
        self._live.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._stop_live()

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.update(self._render_group())
            self._live.stop()
            self._live = None

    # ── Inspection (for tests) ──────────────────────────────────

    def active_row_labels(self) -> List[str]:
        """Labels of the rows that would currently be drawn into the
        Live(Group). Classification, when active, takes priority — it
        only runs before any LLM call, so it can't overlap with tool
        or Thinking rows in practice."""
        if self._classifying_active:
            return [_CLASSIFYING_LABEL]
        if self._active_tools:
            return list(self._active_tools.values())
        if self._thinking_active:
            return [_THINKING_LABEL]
        return []

    # ── Event dispatch ──────────────────────────────────────────

    def _handlers(self) -> Dict[Type[Any], Callable[[Any], None]]:
        return {
            RequestStarted: self._handle_request_started,
            ConversationAssigned: ignore_event,
            DiagnosticClassificationStarted: self._handle_diagnostic_started,
            DiagnosticClassificationCompleted: self._handle_diagnostic_completed,
            LlmCallStarted: self._handle_llm_call_started,
            LlmCallCompleted: ignore_event,
            LlmCallFailed: ignore_event,
            ToolStarted: self._handle_tool_started,
            ToolCompleted: self._handle_tool_done,
            ToolFailed: self._handle_tool_done,
            ChatResponseEmitted: self._handle_chat_response,
            TtsSpeakRequested: self._handle_tts_speak,
            RequestCompleted: self._handle_request_completed,
            RequestFailed: self._handle_request_failed,
            RateLimitDetected: ignore_event,
        }

    # ── Handlers ────────────────────────────────────────────────

    def _handle_request_started(self, event: RequestStarted) -> None:
        id_tag = f" [dim]({event.request_id})[/dim]" if self._show_request_ids else ""
        self._console.print(Panel(
            f"[bold cyan]User Input{id_tag}:[/bold cyan] {event.user_input}",
            expand=False,
        ))

    def _handle_diagnostic_started(self, event: DiagnosticClassificationStarted) -> None:
        self._classifying_active = True
        self._refresh()

    def _handle_diagnostic_completed(self, event: DiagnosticClassificationCompleted) -> None:
        self._classifying_active = False
        self._refresh()

    def _handle_llm_call_started(self, event: LlmCallStarted) -> None:
        # If a tool was active, that tool's row stays; "Thinking" only
        # surfaces once every tool has completed. _refresh() picks the
        # right rendering based on _active_tools.
        self._thinking_active = True
        self._refresh()

    def _handle_tool_started(self, event: ToolStarted) -> None:
        self._active_tools[event.tool_call_id] = event.display_label
        self._refresh()

    def _handle_tool_done(self, event: Any) -> None:
        self._active_tools.pop(event.tool_call_id, None)
        self._refresh()

    def _handle_chat_response(self, event: ChatResponseEmitted) -> None:
        # Stop Live BEFORE printing terminal output. ``transient=True``
        # leaves a blank line on the eventual ``__exit__`` teardown,
        # which would otherwise land between the request-usage line
        # and the session-usage line the mainloop prints next.
        self._active_tools.clear()
        self._thinking_active = False
        self._stop_live()
        from app.coordinator.sanitise import render_block_tags_for_cli
        id_tag = f" [dim]({event.request_id})[/dim]" if self._show_request_ids else ""
        cli_chat = render_block_tags_for_cli(event.text) or event.text
        self._console.print(Panel(
            f"[bold green]Swarpius{id_tag}:[/bold green] {cli_chat}",
            expand=False,
        ))

    def _handle_tts_speak(self, event: TtsSpeakRequested) -> None:
        if self._tts_say_fn is not None and event.text:
            self._tts_say_fn("Coordinator", event.text)

    def _handle_request_completed(self, event: RequestCompleted) -> None:
        self._active_tools.clear()
        self._thinking_active = False
        self._refresh()
        if event.status == "interrupted":
            return
        from app.cli.telemetry import format_usage_summary
        summary_line = format_usage_summary(
            usage=event.usage or {},
            steps=event.total_steps,
            duration_ms=event.total_duration_ms,
        )
        self._console.print(f"[dim]{summary_line}[/dim]")
        if self._on_request_complete is not None:
            self._on_request_complete(
                event.usage or {},
                event.total_steps,
                event.total_duration_ms,
            )

    def _handle_request_failed(self, event: RequestFailed) -> None:
        self._active_tools.clear()
        self._thinking_active = False
        self._stop_live()
        self._console.print(Panel(
            f"[bold red]Request failed:[/bold red] {event.summary}",
            expand=False,
            border_style="red",
        ))

    # ── Rendering ───────────────────────────────────────────────

    def _render_group(self) -> Any:
        from rich.console import Group
        from rich.spinner import Spinner
        # ``style="status.spinner"`` matches the green that
        # ``console.status`` uses by default in Rich's theme — without
        # it, the spinner glyph renders in the terminal default
        # (uncoloured white/grey).
        rows: List[Any] = []
        if self._classifying_active:
            rows.append(Spinner("dots", text=f"[dim]{_CLASSIFYING_LABEL}…[/dim]", style="status.spinner"))
        else:
            for label in self._active_tools.values():
                rows.append(Spinner("dots", text=f"[dim]{label}…[/dim]", style="status.spinner"))
            if not self._active_tools and self._thinking_active:
                rows.append(Spinner("dots", text=f"[dim]{_THINKING_LABEL}…[/dim]", style="status.spinner"))
        return Group(*rows)

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render_group())
