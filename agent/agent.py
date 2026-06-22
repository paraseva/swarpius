from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import threading
import time
import warnings
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

import websockets
from rich.console import Console
from rich.panel import Panel
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from app.cli import history as cli_history
from app.cli import runner as cli_runner
from app.cli.log_routing import ensure_default_log_file, route_info_logs_to_file
from app.cli.session_usage import SessionUsageTracker, format_cost_overview
from app.cli.startup_banner import collect_banner_facts, copyright_notice, render_banner
from app.cli.tap_window import is_recent
from app.cli.validation_summary import format_summary
from app.constants import (
    CHANNEL_ROON_CORE_STATUS,
    CHAT_PANEL_AGENTS,
    WS_MAX_FRAME_SIZE,
    WS_MAX_QUEUE_SIZE,
)
from app.coordinator.request_flow import (
    arbitrate_interrupt as _arbitrate_interrupt_impl,
)
from app.coordinator.request_flow import process_request as _process_request_impl
from app.data_paths import (
    cli_history_path,
    default_log_path,
    ensure_dirs,
    ensure_stop_marker_asset,
    messages_db_path,
)
from app.io import AppIO
from app.io.cost_ledger import CostLedger, set_cost_ledger
from app.io.history_retention import prune_history
from app.io.message_store import SqliteMessageStore, set_message_store
from app.io.state_db import StateDb
from app.io.static_files import resolve_dist_dir, serve_dist
from app.io.websocket_flow import websocket_handler as _websocket_handler_impl
from app.runtime.persistence import PersistenceManager
from app.runtime.request_logger import RequestIdGenerator, cleanup_old_logs, get_retention_days
from app.runtime.server_logger import cleanup_old_server_logs
from app.runtime.state import RuntimeState
from app.settings.env_file import ensure_env_file_exists, load_env_into_process
from roon_core.auth import default_core_id_path, default_token_path
from tts.tts import speak_text

# Bundle first-run: copy the .env.template into the user-data dir
# so the user has a file to edit (or the Settings UI can read from).
# Does nothing in source / Docker mode.
ensure_env_file_exists()
load_env_into_process()

# ── Logging configuration ────────────────────────────────────────
from app.runtime.log_format import (  # noqa: E402
    LOG_DATEFMT,
    LOG_FORMAT,
    UnclosedSessionFilter,
    add_file_handler_once,
    make_file_handler,
    quiet_console_stderr,
)

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATEFMT,
)

from app.settings import get_settings as _get_settings  # noqa: E402

_log_file = _get_settings().log_file
if _log_file:
    add_file_handler_once(Path(_log_file))
    # Mirrors the CLI/bundle pattern: when the user has asked for
    # logs in a file, their terminal stays quiet (WARNING+ only).
    quiet_console_stderr()

# Suppress noisy websockets handshake errors (browser refresh closes connection mid-upgrade)
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
# Suppress LiteLLM internal logging noise
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)
# Drop aiohttp's GC-time "Unclosed client session/connector" noise
# (the analyser's LiteLLM calls leave sessions for the GC to reap).
logging.getLogger("asyncio").addFilter(UnclosedSessionFilter())


# LiteLLM emits these WARNINGs at import time when ``botocore`` is
# absent — one per AWS event-stream service it tries to pre-load
# (bedrock-runtime, sagemaker-runtime, ...). We only speak via
# Anthropic / OpenAI / Ollama etc., never AWS event-stream, so none
# of them carry actionable signal for users. Drop the family with a
# single substring match on the shared "could not pre-load …
# response stream shape" phrasing.
class _DropEventStreamPreloadWarning(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not ("could not pre-load" in msg and "response stream shape" in msg)


for _name in ("LiteLLM", "litellm"):
    logging.getLogger(_name).addFilter(_DropEventStreamPreloadWarning())
# roonapi attaches its own StreamHandler at import (with a different date-prefixed
# format), so every roonapi line was being printed twice — once via their handler
# and once via root propagation. Strip their handler so only our format fires.
_roonapi_logger = logging.getLogger("roonapi")
for _handler in list(_roonapi_logger.handlers):
    _roonapi_logger.removeHandler(_handler)
# LiteLLM's async logging worker creates unawaited coroutines on shutdown
warnings.filterwarnings(
    "ignore",
    message=r"coroutine.*was never awaited",
    category=RuntimeWarning,
)

RUN_MODE = "cli"
SHOW_REQUEST_IDS = False  # CLI-only; flipped on by --show-request-ids

console: Optional[Console] = None
ws_clients: "set[ServerConnection]" = set()
ws_event_loop: Optional[asyncio.AbstractEventLoop] = None


def get_console() -> Console:
    global console
    if console is None:
        console = Console()
    return console


def print_brand_banner() -> None:
    """Print the trademark + copyright banner. Shown once at startup in
    both CLI and WS modes."""
    get_console().print(Panel(copyright_notice(), expand=False, border_style="yellow"))


io_bridge = AppIO(
    run_mode_getter=lambda: RUN_MODE,
    console_getter=get_console,
    speak_text_coro=speak_text,
    ws_clients=ws_clients,
    get_ws_event_loop=lambda: ws_event_loop,
)


def _ws_send(channel: str, payload: Any, meta: Any = None) -> None:
    io_bridge.ws_send(channel, payload, meta=meta)


def sayit(agent_name: str, message: str) -> None:
    io_bridge.sayit(
        agent_name=agent_name,
        message=message,
        chat_panel_agents=CHAT_PANEL_AGENTS,
    )


runtime = RuntimeState()
runtime.configure_io_callbacks(
    run_mode_getter=lambda: RUN_MODE,
    ws_send_callback=_ws_send,
    get_ws_event_loop=lambda: ws_event_loop,
)

# Shared state DB: StateDb owns the one connection backing both the
# transcript (message store) and the persisted runtime state. History and
# state persist across restarts — there is no clear-on-boot.
_state_db = StateDb(messages_db_path())
_session_store = SqliteMessageStore(_state_db)
set_message_store(_session_store)

# Cost ledger shares the same DB; every LLM agent records its spend here for
# the cost dashboard. Available in both CLI and WS modes (this runs on import).
set_cost_ledger(CostLedger(_state_db))

# Prune persisted history past its retention windows before anything reads it.
_retention_settings = _get_settings()
prune_history(
    _state_db,
    chat_days=_retention_settings.chat_history_retention_days,
    diagnostics_days=_retention_settings.diagnostics_retention_days,
    listening_days=_retention_settings.listening_history_retention_days,
    now_ms=int(time.time() * 1000),
)

# Restore working memory now; Roon-scoped state restores once the connection
# exists (RuntimeState.ensure_initialised). Request completion commits via
# runtime.persist_state().
_persistence_manager = PersistenceManager(_state_db)
runtime.attach_persistence(_persistence_manager)

_server_start_ms = int(time.time() * 1000)


def get_server_start_ms() -> int:
    return _server_start_ms


def _arbitrate_interrupt(active_request: str, incoming_request: str) -> Any:
    return _arbitrate_interrupt_impl(
        runtime=runtime,
        active_request=active_request,
        incoming_request=incoming_request,
        ws_send_fn=_ws_send,
    )


def process_request(
    user_input: str,
    cancel_event: Optional[threading.Event] = None,
    request_id_generator: Optional[RequestIdGenerator] = None,
    on_request_complete: Optional[Callable[[dict, int, int], None]] = None,
    event_bus: Optional[Any] = None,
    client_msg_id: Optional[str] = None,
) -> None:
    """Top-level entry. Wires WS broadcaster + bus for WS mode if the
    caller didn't supply its own bus. The CLI loop constructs its own
    bus + CliRenderer (see CLI mainloop) and passes them in via
    ``event_bus``."""
    from app.coordinator.event_bus import EventBus
    if event_bus is None:
        bus = EventBus()
        if RUN_MODE == "ws":
            from app.io.ws_broadcaster import WsBroadcaster
            ws_broadcaster = WsBroadcaster(ws_send_fn=_ws_send, runtime=runtime)
            bus.subscribe(ws_broadcaster.handle)
    else:
        bus = event_bus
    _process_request_impl(
        runtime=runtime,
        user_input=user_input,
        cancel_event=cancel_event,
        request_id_generator=request_id_generator,
        run_mode_label=RUN_MODE,
        event_bus=bus,
        client_msg_id=client_msg_id,
    )


_auto_shutdown: Optional[Any] = None


async def websocket_handler(websocket: ServerConnection) -> None:
    await _websocket_handler_impl(
        websocket=websocket,
        runtime=runtime,
        ws_clients=ws_clients,
        process_request_fn=process_request,
        arbitrate_interrupt_fn=_arbitrate_interrupt,
        ws_send_fn=_ws_send,
        auto_shutdown=_auto_shutdown,
    )


_HTTP_REASON = {
    200: "OK",
    404: "Not Found",
    500: "Internal Server Error",
}


def _make_http_handler(dist_dir):
    """Build a websockets process_request callback that serves the
    bundled web client. Requests to /ws fall through to the WS
    upgrade; anything else is served as static content from dist_dir.
    """
    def _handler(connection: ServerConnection, request: Request) -> Optional[Response]:
        path = request.path
        if (
            path == "/ws" or path.startswith("/ws?")
            or path == "/tts" or path.startswith("/tts?")
        ):
            return None
        status, headers, body = serve_dist(dist_dir, path)
        return Response(
            status_code=status,
            reason_phrase=_HTTP_REASON.get(status, ""),
            headers=Headers(headers.items()),
            body=body,
        )

    return _handler


# Global shutdown event — set on SIGINT to stop long-running tool operations.
server_shutdown_event = threading.Event()


def _open_browser_for_bundle(host: str, port: int) -> None:
    """Open the user's default browser to the served UI on startup.

    ``0.0.0.0`` and similar bind-all addresses are rewritten to
    ``localhost`` because most browsers reject the former. Any
    failure (no display, no default browser, exception) is swallowed
    so the agent keeps running headless.
    """
    display_host = "localhost" if host in {"0.0.0.0", "::", ""} else host
    url = f"http://{display_host}:{port}/"
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        # No display, no default browser, or webbrowser misbehaviour
        # — swallow so the agent keeps running headless. The docstring
        # above documents this contract.
        pass


def _print_required_config_hint(missing: list[str], mode: str) -> None:
    """Mode-aware hint shown when required env vars are missing.

    WS mode points the user at the Settings page; CLI mode tells them
    where the .env file lives and what to put in it. Either way the
    list of missing keys comes from ``required_config_missing()``.
    """
    from app.settings.env_file import resolve_env_path
    cons = get_console()
    env_path = resolve_env_path()

    if mode == "ws":
        body = (
            "[bold]Required config missing[/bold]\n\n"
            f"Missing: [yellow]{', '.join(missing)}[/yellow]\n\n"
            "Open the [bold]Settings[/bold] page in your browser to "
            "fill these in, then [bold]Save & Validate[/bold] and [bold]Restart[/bold]."
        )
    else:
        example_lines = []
        for key in missing:
            if key == "LLM_MODEL":
                example_lines.append(
                    "  LLM_MODEL=anthropic/claude-sonnet-4-6"
                    "   # provider/model — any LiteLLM-supported provider",
                )
                # LLM_MODEL being empty means we don't yet know which
                # provider key to demand. Surface it now so the user can
                # set both in one pass instead of restarting twice.
                example_lines.append(
                    "  LLM_API_KEY_<PROVIDER>=<your-api-key>"
                    "   # e.g. LLM_API_KEY_ANTHROPIC=sk-ant-...",
                )
            elif key.startswith("LLM_API_KEY_"):
                example_lines.append(
                    f"  {key}=<your-api-key>",
                )
            else:
                example_lines.append(f"  {key}=<value>")
        body = (
            "[bold]Required config missing[/bold]\n\n"
            f"Missing: [yellow]{', '.join(missing)}[/yellow]\n\n"
            f"Edit [bold cyan]{env_path}[/bold cyan] and set:\n\n"
            + "\n".join(example_lines)
            + "\n\nThen restart. See [dim].env.template[/dim] in the "
            "agent directory for the full list of options."
        )

    cons.print(
        Panel(
            body,
            border_style="red",
            expand=False,
            padding=(1, 2),
        ),
    )


def _run_boot_validation_sync():
    """Drive ``ConfigValidator.validate()`` from a sync context.

    Returns the final ``ValidationStatus`` or ``None`` if the call
    itself blew up (logged but non-fatal — the rest of boot proceeds
    and surface-level errors will materialise normally).
    """
    from app.settings.validation import get_validator

    try:
        return asyncio.run(get_validator().validate())
    except Exception:
        logging.getLogger("swarpius.boot").exception(
            "Boot-time validation raised",
        )
        return None


def _format_validation_errors_for_cli(status) -> str:
    """Build the body of the CLI-mode validation-failure panel.

    One line per failed agent row showing agent / model / error_kind /
    detail. Disabled agents and PASSED rows are omitted to keep the
    output tight."""
    lines = []
    for r in status.results:
        if not r.enabled or r.ok is None or r.ok:
            continue
        kind = r.error_kind or "other"
        detail = r.detail or ""
        lines.append(
            f"  [bold]{r.agent}[/bold] ({r.model or '—'}): "
            f"[yellow]{kind}[/yellow] — {detail}",
        )
    return "\n".join(lines) if lines else "  (no per-row details)"


def _print_roon_setup_panel_if_first_run() -> bool:
    """Print the first-run Roon-setup panel if no auth tokens are on
    disk. Returns True if the panel was printed."""
    cons = get_console()
    have_existing_auth = (
        default_core_id_path().exists() and default_token_path().exists()
    )

    if not have_existing_auth:
        cons.print(
            Panel(
                "[bold]Roon setup needed[/bold]\n\n"
                "Swarpius is now waiting for you to authorise it inside Roon.\n\n"
                "  [bold cyan]1.[/bold cyan] Open the Roon app\n"
                "  [bold cyan]2.[/bold cyan] Go to [bold]Settings → Extensions[/bold]\n"
                "  [bold cyan]3.[/bold cyan] Find [bold]Swarpius[/bold] and click "
                "[bold green]Enable[/bold green]\n\n"
                "Swarpius will continue automatically once you do.",
                border_style="yellow",
                expand=False,
                padding=(1, 2),
            ),
        )
        cons.print()

    return not have_existing_auth


def _roon_init_with_console_feedback() -> None:
    """Synchronous Roon init with console spinner. CLI-mode path —
    blocks until pairing completes. WS mode uses
    ``_start_roon_init_async`` instead."""
    core_id_p = default_core_id_path()
    token_p = default_token_path()

    cons = get_console()
    have_existing_auth = core_id_p.exists() and token_p.exists()

    _print_roon_setup_panel_if_first_run()

    with cons.status(
        "[bold cyan]Connecting to Roon Core…[/bold cyan]", spinner="dots",
    ) as status:
        def _lifecycle(msg: str) -> None:
            runtime.roon_status_message = msg
            status.update(f"[bold cyan]{msg}[/bold cyan]")
        runtime.roon_lifecycle_callback = _lifecycle

        init_done = threading.Event()

        def _stuck_hint() -> None:
            if init_done.wait(timeout=10.0):
                return
            if have_existing_auth:
                cons.print(
                    "[yellow]Still connecting. If you de-authorised the "
                    "Swarpius extension in Roon, re-approve it in "
                    "Roon Settings → Extensions. Or delete "
                    f"{token_p} + {core_id_p.name} and restart "
                    "to force fresh discovery.[/yellow]",
                )
            else:
                cons.print(
                    "[yellow]Still waiting for you to authorise Swarpius "
                    "in Roon. Open Roon → Settings → Extensions → Enable "
                    "'Swarpius' to continue.[/yellow]",
                )

        threading.Thread(target=_stuck_hint, daemon=True).start()

        try:
            runtime.ensure_initialised()
            runtime.roon_state = "paired"
        except Exception as exc:
            runtime.roon_state = "failed"
            runtime.roon_failure_reason = str(exc)
            raise
        finally:
            init_done.set()
            runtime.roon_lifecycle_callback = None


def _start_roon_init_async(
    broadcast_feature_availability: Callable[[], None],
) -> threading.Thread:
    """WS-mode Roon init on a background daemon thread.

    Lets the WS server bind before Roon pairing completes so the
    browser can render the Roon-setup view. State transitions update
    ``runtime.roon_state`` / ``roon_status_message`` /
    ``roon_failure_reason`` (consumed by the feature-availability
    payload) and trigger a broadcast on each change.

    When required LLM config is missing we don't run init at all —
    ``ensure_initialised`` would fail on the LLM client step before
    ever touching Roon, surfacing a misleading "Roon failed" message.
    The user fills in Settings, the Restart flow respawns the
    process, and the fresh run reaches this path with config valid.
    """
    cons = get_console()
    from app.settings import required_config_missing as _required_config_missing

    missing = _required_config_missing()
    if missing:
        runtime.roon_state = "awaiting_config"
        runtime.roon_status_message = (
            "Waiting for required config — fill in the Settings page in your browser."
        )
        runtime.roon_failure_reason = None
        _print_required_config_hint(missing, mode="ws")
        broadcast_feature_availability()
        # Return a do-nothing thread so the caller's API stays consistent.
        return threading.Thread(target=lambda: None, daemon=True)

    _print_roon_setup_panel_if_first_run()

    def _on_lifecycle(msg: str) -> None:
        runtime.roon_status_message = msg
        cons.print(f"[cyan]Roon:[/cyan] {msg}")
        broadcast_feature_availability()

    runtime.roon_lifecycle_callback = _on_lifecycle
    runtime.roon_state = "initialising"
    runtime.roon_status_message = "Checking providers & services…"
    runtime.roon_failure_reason = None

    def _thread_body() -> None:
        # Live LLM validation runs here (not in the caller) — the
        # caller is the WS event loop thread, where asyncio.run()
        # would explode with "cannot be called from a running event
        # loop". Bad keys / unknown model names surface clearly
        # before ensure_initialised tries to build LLM clients.
        validation_status = _run_boot_validation_sync()
        if validation_status is not None and validation_status.state.value == "failed":
            runtime.roon_state = "awaiting_config"
            runtime.roon_status_message = (
                "LLM validation failed — see Settings for details."
            )
            runtime.roon_failure_reason = None
            cons.print(
                "[bold red]LLM validation failed.[/bold red] "
                "Open Settings in your browser to fix.",
            )
            broadcast_feature_availability()
            return
        try:
            runtime.ensure_initialised()
            runtime.roon_state = "paired"
            runtime.roon_failure_reason = None
            runtime.roon_status_message = "Connected"
            cons.print("[bold green]Roon connected — agent ready.[/bold green]")
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            # If roon_connection was never assigned the failure is from
            # pre-Roon init steps (LLM client / model spec / etc.) — label
            # it generically so the user isn't told "Roon failed" for an
            # LLM config problem.
            roon_started = runtime.roon_connection is not None
            label = "Roon setup failed" if roon_started else "Startup failed"
            runtime.roon_state = "failed"
            runtime.roon_failure_reason = str(exc)
            runtime.roon_status_message = f"{label}: {exc}"
            cons.print(f"[bold red]{label}:[/bold red] {exc}")
        finally:
            runtime.roon_lifecycle_callback = None
            broadcast_feature_availability()
            if runtime.roon_state == "paired":
                # Deliver the now-ready zone state to any client that
                # connected mid-pairing (its connect-time snapshot was empty).
                runtime.broadcast_roon_ready()

    thread = threading.Thread(
        target=_thread_body, name="swarpius-roon-init", daemon=True,
    )
    thread.start()
    return thread


async def start_websocket_server(
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    global ws_event_loop
    ws_event_loop = asyncio.get_running_loop()
    ensure_dirs()
    ensure_stop_marker_asset()
    runtime.shutdown_event = server_shutdown_event

    # Bundle's console is user-facing: route INFO chatter to file and
    # silence stderr. Source / Docker keep INFO on stderr (operators
    # want startup logs visible) AND get a default file handler for
    # post-mortem. Either mode can be overridden by ``LOG_FILE``.
    from app.data_paths import _running_from_bundle
    bundle_log_path: Optional[Path] = None
    if _running_from_bundle():
        bundle_log_path = route_info_logs_to_file(default_log_path())
    else:
        ensure_default_log_file(default_log_path())

    print_brand_banner()

    removed = cleanup_old_logs(retention_days=get_retention_days())
    cleanup_old_server_logs(retention_days=get_retention_days())
    if removed:
        get_console().print(f"[dim]Cleaned up {removed} old log folder(s)[/dim]")

    # The main block calls ``os._exit(0)`` once this future resolves
    # and the WS server has closed, bypassing asyncio's default-
    # executor join (which blocks up to 5 min on a stuck blocking
    # tool call). A second Ctrl+C is the defensive escape hatch if
    # the graceful close itself hangs.
    stop_future: asyncio.Future[None] = ws_event_loop.create_future()
    shutdown_state = {"requested": False}

    def _signal_shutdown() -> None:
        if shutdown_state["requested"]:
            get_console().print("[bold red]Forcing exit.[/bold red]")
            os._exit(0)
        shutdown_state["requested"] = True
        server_shutdown_event.set()
        if not stop_future.done():
            stop_future.set_result(None)
        get_console().print("[dim]Shutting down…[/dim]")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            ws_event_loop.add_signal_handler(sig, _signal_shutdown)
        except NotImplementedError:
            # add_signal_handler not supported on Windows —
            # Ctrl+C will still raise KeyboardInterrupt via asyncio.run()
            pass

    # Exposed so the Settings "Restart" action can request a
    # clean exit without going through a signal.
    runtime.signal_shutdown = _signal_shutdown

    # Bundle-only: quit if nobody connects post-launch, and quit when
    # the last client disconnects. Mirrors a native app's window-close
    # semantics. Source / Docker / WSL leave _auto_shutdown as None.
    from app.data_paths import _running_from_bundle
    global _auto_shutdown
    if _running_from_bundle():
        from app.runtime.auto_shutdown import AutoShutdown
        _auto_shutdown = AutoShutdown(ws_event_loop, _signal_shutdown)
        _auto_shutdown.start_startup_grace()

    dist_dir = resolve_dist_dir()
    http_handler = _make_http_handler(dist_dir) if dist_dir is not None else None

    def _broadcast_from_background_thread() -> None:
        loop = ws_event_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(runtime._broadcast_feature_availability)

    def _broadcast_validation_status(payload: dict) -> None:
        loop = ws_event_loop
        if loop is None or not loop.is_running():
            return
        from app.constants import CHANNEL_VALIDATION_STATUS
        loop.call_soon_threadsafe(
            runtime._ws_send_callback,
            CHANNEL_VALIDATION_STATUS,
            payload,
        )

    from app.settings.validation import set_broadcast as _set_validation_broadcast
    _set_validation_broadcast(_broadcast_validation_status)

    async with websockets.serve(
        websocket_handler,
        host,
        port,
        max_size=WS_MAX_FRAME_SIZE,
        max_queue=WS_MAX_QUEUE_SIZE,
        process_request=http_handler,
    ):
        # Banner first — everything else (Roon discovery, API-key checks,
        # analyser loop) runs over this WS endpoint, so the user wants
        # to see "where do I point my browser?" before any of that.
        if dist_dir is not None:
            banner_lines = [
                f"[bold green]Server listening on http://{host}:{port}/[/bold green]",
                f"[dim]Web UI served from {dist_dir}[/dim]",
                f"[dim]WebSocket endpoint: ws://{host}:{port}/ws[/dim]",
            ]
            if bundle_log_path is not None:
                banner_lines.append(f"[dim]Logs: {bundle_log_path}[/dim]")
            get_console().print(Panel("\n".join(banner_lines), expand=False))
            from app.data_paths import should_auto_open_browser
            if should_auto_open_browser():
                _open_browser_for_bundle(host, port)
        else:
            get_console().print(
                Panel(
                    f"[bold green]WebSocket server listening on ws://{host}:{port}/ws[/bold green]",
                    expand=False,
                ),
            )

        # Roon init + provider validation come after the banner. Both
        # run on background daemon threads so the WS server can accept
        # connections in parallel.
        _start_roon_init_async(_broadcast_from_background_thread)

        from app.runtime.backend_health import start_backend_health_loop
        start_backend_health_loop(
            server_shutdown_event,
            on_change=_broadcast_from_background_thread,
        )

        # Roon Core connection-health watcher — surfaces Core drops to the
        # UI (independent of the agent↔browser WS, which stays up).
        from app.roon.core_health import start_roon_health_loop
        start_roon_health_loop(
            server_shutdown_event,
            is_connected=lambda: bool(
                runtime.roon_connection and runtime.roon_connection.is_connected
            ),
            emit=lambda state: _ws_send(CHANNEL_ROON_CORE_STATUS, {"state": state}),
        )

        # Passive analyser background loop (opt-in via env flag).
        _settings_for_analyser = _get_settings()
        if _settings_for_analyser.enable_passive_analyser:
            from analyser.loop import start_background_loop
            start_background_loop(
                server_shutdown_event,
                interval_minutes=_settings_for_analyser.analyser_interval_minutes,
                staleness_minutes=_settings_for_analyser.analyser_staleness_minutes,
                batch_size=_settings_for_analyser.analyser_batch_size,
            )
            get_console().print(
                f"[dim]Passive analyser loop running "
                f"(every {_settings_for_analyser.analyser_interval_minutes}m, "
                f"staleness {_settings_for_analyser.analyser_staleness_minutes}m, "
                f"batch {_settings_for_analyser.analyser_batch_size}).[/dim]",
            )

        _ = await stop_future


def run_cli_loop() -> None:
    ensure_dirs()
    ensure_stop_marker_asset()
    console = get_console()
    print_brand_banner()

    # Route INFO to a file so the terminal stays clean during the
    # spinner; everything from here on is muted from stderr.
    log_file_path = route_info_logs_to_file(default_log_path())

    # CLI mode has no UI to drive a config fix — bail with a clear
    # hint instead of letting ensure_initialised() raise a generic
    # ValueError partway through init.
    from app.settings import required_config_missing as _required_config_missing
    missing = _required_config_missing()
    if missing:
        _print_required_config_hint(missing, mode="cli")
        sys.exit(1)

    # Run live provider validation before Roon init. Catches expired
    # keys / unknown model names cleanly here rather than partway through
    # the request loop. Only the coordinator gates; sub-agent and backend
    # failures are reported and we continue. The spinner keeps a slow
    # probe from looking like a hang.
    _validation_started = time.monotonic()
    with console.status("[bold]Checking providers & services…", spinner="dots"):
        validation_status = _run_boot_validation_sync()
    _validation_elapsed = time.monotonic() - _validation_started
    if validation_status is not None and validation_status.state.value == "failed":
        console.print(
            Panel(
                "[bold]LLM validation failed[/bold]\n\n"
                + _format_validation_errors_for_cli(validation_status)
                + "\n\nEdit your .env and try again.",
                border_style="red",
                expand=False,
                padding=(1, 2),
            ),
        )
        sys.exit(1)
    if validation_status is not None:
        console.print(format_summary(validation_status, elapsed=_validation_elapsed))

    # Shared between CLI and WS modes — see _roon_init_with_console_feedback
    # for the visibility treatment (first-run setup panel, spinner, stuck-hint).
    # A startup failure exits with a clean message (not a traceback); the WS
    # path surfaces the same failures in the web UI instead.
    try:
        _roon_init_with_console_feedback()
    except Exception as exc:
        _report_startup_failure(exc)

    removed = cleanup_old_logs(retention_days=get_retention_days())
    cleanup_old_server_logs(retention_days=get_retention_days())
    if removed:
        console.print(f"[dim]Cleaned up {removed} old log folder(s)[/dim]")

    from app.settings import get_settings
    settings = get_settings()
    facts = collect_banner_facts(runtime, settings, log_file_path)
    console.print(render_banner(facts))
    console.print()

    # Surface Roon Core drops/reconnects so a CLI user knows why playback
    # stopped working — WS mode shows a modal, CLI prints a line. Daemon
    # thread; dies with the process on exit.
    from app.roon.core_health import format_roon_status_message, start_roon_health_loop
    start_roon_health_loop(
        threading.Event(),
        is_connected=lambda: bool(
            runtime.roon_connection and runtime.roon_connection.is_connected
        ),
        emit=lambda state: console.print(format_roon_status_message(state)),
    )

    history_path = cli_history_path()
    cli_history.load_history(history_path)
    # Use the process-level generator (restored across restarts) when wired.
    id_generator = runtime.request_id_generator or RequestIdGenerator()
    session_usage = SessionUsageTracker()

    # Route CLI-mode TTS disable notices through Rich (yellow one-liner)
    # instead of the verbose stderr WARNING format. The log-file
    # mirror keeps the message in $SWARPIUS_DATA_DIR/swarpius.log for
    # post-mortem.
    from tts.tts import set_notice_callback as _set_tts_notice_callback

    def _tts_notice(message: str) -> None:
        console.print(f"[yellow]{message}[/yellow]")
        logging.getLogger("tts.tts").info("%s", message)

    _set_tts_notice_callback(_tts_notice)

    def _on_first_interrupt() -> None:
        console.print("[dim]Cancelling… (Ctrl+C again to exit)[/dim]")

    def _on_second_interrupt() -> None:
        console.print("[dim]Stopping.[/dim]")

    # Two-tap exit at the prompt: first Ctrl+C arms exit and prints
    # a "press again" hint; a second Ctrl+C within 2 seconds quits.
    # After that window the arm expires so a stray Ctrl+C 10 minutes
    # later doesn't pre-arm exit.
    prompt_exit_armed_at: Optional[float] = None

    try:
        while True:
            try:
                console.print(">> ", end="", highlight=False)
                command = input().strip()
            except EOFError:
                # Ctrl+D: print a newline so the next shell prompt
                # doesn't land on the same line as ">>".
                print()
                break
            except KeyboardInterrupt:
                if is_recent(prompt_exit_armed_at, time.monotonic()):
                    print()
                    break
                console.print("[dim](press Ctrl+C again within 2s to exit, or /exit)[/dim]")
                prompt_exit_armed_at = time.monotonic()
                continue
            prompt_exit_armed_at = None
            if not command:
                continue
            if command == "/exit":
                break
            if command == "/usage":
                if session_usage.has_data():
                    console.print(f"[dim]{session_usage.format_detailed()}[/dim]")
                else:
                    console.print("[dim]No requests yet this session.[/dim]")
                overview = format_cost_overview()
                if overview:
                    console.print(f"[dim]{overview}[/dim]")
                console.print()
                continue

            from app.cli.renderer import CliRenderer
            from app.coordinator.event_bus import EventBus

            cli_bus = EventBus()
            cli_renderer = CliRenderer(
                rich_console=console,
                tts_say_fn=sayit,
                on_request_complete=lambda usage, steps, dur_ms:
                    session_usage.accumulate(usage, steps=steps, duration_ms=dur_ms),
                show_request_ids=SHOW_REQUEST_IDS,
            )
            cli_bus.subscribe(cli_renderer.handle)

            with cli_renderer:
                def _target(cancel_event: threading.Event) -> None:
                    process_request(
                        user_input=command,
                        cancel_event=cancel_event,
                        request_id_generator=id_generator,
                        event_bus=cli_bus,
                    )

                exit_requested, error = cli_runner.run_request_with_cancel(
                    target=_target,
                    on_first_interrupt=_on_first_interrupt,
                    on_second_interrupt=_on_second_interrupt,
                )

            if error is not None:
                console.print(f"[red]Request failed: {error}[/red]")
            if session_usage.has_data():
                console.print(f"[dim]{session_usage.format_summary()}[/dim]")
            if exit_requested:
                break
            # Visual gap between requests so the history doesn't
            # crowd the prompt.
            console.print()
    finally:
        cli_history.save_history(history_path)
    print("Goodbye!")


def _report_startup_failure(exc: BaseException):
    """Surface a startup failure as a clean one-line message, then exit(1).

    Startup failures — no Roon Cores discovered, an unreachable configured
    URL, an authorisation timeout, an LLM/config error — are user-actionable,
    so the traceback only adds console noise. The full trace still goes to
    LOG_FILE when one is configured.
    """
    if _log_file:
        startup_log = logging.getLogger("swarpius.startup")
        startup_log.setLevel(logging.ERROR)
        startup_log.propagate = False  # skip root's stderr handler
        startup_log.addHandler(make_file_handler(Path(_log_file)))
        startup_log.error("Startup failure: %s", exc, exc_info=True)
    get_console().print(f"[bold red]Startup failed:[/bold red] {exc}")
    sys.exit(1)


def main() -> None:
    """Agent entrypoint. Parses argv, dispatches to CLI loop or WS server.

    Wrapped in a function (rather than a bare ``if __name__`` block) so
    the supervisor (``swarpius.py``) can drive launch and restart cleanly
    via ``subprocess`` — the supervisor calls ``python agent.py`` and
    forwards argv unchanged.
    """
    global RUN_MODE, SHOW_REQUEST_IDS

    parser = argparse.ArgumentParser(
        description="Swarpius — interactive CLI by default; --ws to run as a WebSocket server.",
    )
    parser.add_argument(
        "--ws",
        action="store_true",
        help=(
            "Run as a WebSocket server (for the web client / Docker). "
            "Bind via SWARPIUS_WS_HOST / SWARPIUS_WS_PORT env vars "
            "(defaults 127.0.0.1:8080)."
        ),
    )
    parser.add_argument(
        "--show-request-ids",
        action="store_true",
        help="CLI mode: show request IDs (rq-cNN-NNNN) on user/agent panels for log lookup.",
    )
    args = parser.parse_args()
    SHOW_REQUEST_IDS = args.show_request_ids

    try:
        if args.ws:
            RUN_MODE = "ws"
            _settings = _get_settings()
            try:
                asyncio.run(
                    start_websocket_server(
                        host=_settings.ws_host,
                        port=_settings.ws_port,
                    ),
                )
            except KeyboardInterrupt:
                # ``add_signal_handler`` isn't supported on Windows,
                # so SIGINT bubbles up here rather than routing through
                # ``_signal_shutdown``.
                pass
            # Brief drain so background daemons (analyser scan, TTS
            # health probe) that just noticed server_shutdown_event
            # can finish their current iteration — particularly any
            # in-flight disk writes — before we hard-exit. 300 ms is
            # negligible to the user and meaningfully shrinks the
            # bad-timing window for atomic-write tmp orphans.
            time.sleep(0.3)
            from app.data_paths import _running_in_docker
            from app.runtime.restart_signal import is_restart_requested, perform_restart
            # In Docker, exit zero — compose's restart policy respawns
            # the container, which picks up host .env edits cleanly.
            # Outside Docker, perform_restart exits with the sentinel
            # code that the swarpius supervisor recognises as "respawn
            # me from scratch".
            if is_restart_requested() and not _running_in_docker():
                perform_restart()
            else:
                # Skip asyncio's default-executor join (up to 5 min on
                # an in-flight blocking tool call). The WS server has
                # already closed cleanly above.
                os._exit(0)
        else:
            RUN_MODE = "cli"
            run_cli_loop()
    except ConnectionError as exc:
        _report_startup_failure(exc)


if __name__ == "__main__":
    main()
