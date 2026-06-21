from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Optional
from urllib.parse import parse_qs, urlparse

import websockets.exceptions
from websockets.asyncio.server import ServerConnection

from app.constants import (
    CHANNEL_AGENT_OUTPUTS,
    CHANNEL_ANALYSIS_DETAIL_REQUEST,
    CHANNEL_ANALYSIS_DETAIL_RESPONSE,
    CHANNEL_ANALYSIS_FEEDBACK_REQUEST,
    CHANNEL_ANALYSIS_FEEDBACK_RESPONSE,
    CHANNEL_ANALYSIS_LIST_REQUEST,
    CHANNEL_ANALYSIS_LIST_RESPONSE,
    CHANNEL_ANALYSIS_METRICS_REQUEST,
    CHANNEL_ANALYSIS_METRICS_RESPONSE,
    CHANNEL_ANALYSIS_REQUEST_LOGS_REQUEST,
    CHANNEL_ANALYSIS_REQUEST_LOGS_RESPONSE,
    CHANNEL_ANALYSIS_RESULT_HANDLE_REQUEST,
    CHANNEL_ANALYSIS_RESULT_HANDLE_RESPONSE,
    CHANNEL_ANALYSIS_RUN_REQUEST,
    CHANNEL_ANALYSIS_RUN_RESPONSE,
    CHANNEL_ANALYSIS_UPDATE,
    CHANNEL_CHAT,
    CHANNEL_CLEAR_CONVERSATION_REQUEST,
    CHANNEL_CLEAR_CONVERSATION_RESPONSE,
    CHANNEL_DEFAULT_ZONE_UPDATE,
    CHANNEL_ERRORS,
    CHANNEL_FEATURE_AVAILABILITY,
    CHANNEL_FEATURE_VERIFY_REQUEST,
    CHANNEL_IMAGE_REQUEST,
    CHANNEL_IMAGE_RESPONSE,
    CHANNEL_LLM_DIAGNOSTICS,
    CHANNEL_OPEN_DATA_FOLDER_REQUEST,
    CHANNEL_QUEUE_UPDATES,
    CHANNEL_ROON_CONTROL_REQUEST,
    CHANNEL_ROON_CONTROL_RESPONSE,
    CHANNEL_ROON_CORE_STATUS,
    CHANNEL_ROON_EXPLORER_REQUEST,
    CHANNEL_ROON_EXPLORER_RESPONSE,
    CHANNEL_SESSION_CONTROL_REQUEST,
    CHANNEL_SESSION_CONTROL_RESPONSE,
    CHANNEL_SETTINGS_READ_REQUEST,
    CHANNEL_SETTINGS_READ_RESPONSE,
    CHANNEL_SETTINGS_RELOAD_REQUEST,
    CHANNEL_SETTINGS_RELOAD_RESPONSE,
    CHANNEL_SETTINGS_SAVE_REQUEST,
    CHANNEL_SETTINGS_SAVE_RESPONSE,
    CHANNEL_SETTINGS_TEST_REQUEST,
    CHANNEL_SETTINGS_TEST_RESPONSE,
    CHANNEL_ZONE_SNAPSHOTS,
    CLOSE_CODE_SESSION_TAKEOVER,
    PENDING_MESSAGES_MAXLEN,
)
from app.data_paths import analysis_dir, conversation_logs_dir
from app.exceptions import UnsupportedActionError
from app.io.redact import redact_secrets
from app.io.ws_path_validators import (
    validate_conversation_id,
    validate_date,
    validate_image_key,
    validate_request_id,
)
from app.runtime.request_logger import RequestIdGenerator

_log = logging.getLogger("swarpius.websocket_flow")


def _analysis_logs_root() -> Path:
    return conversation_logs_dir()


def _analysis_metrics_path() -> Path:
    return analysis_dir() / "metrics.jsonl"


def _save_request_wants_restart(raw_body: str) -> bool:
    """Parse a settings-save-request body to see if the caller asked
    for a post-save restart. Returns False on any parse error so a
    malformed request doesn't trigger an accidental shutdown."""
    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        return False
    return bool(payload.get("restart"))


async def _revalidate_after_save() -> None:
    """Re-run config validation after a successful save and toggle
    pending_restart based on the outcome.

    The validator broadcasts state transitions itself; we just need to
    set the restart flag once the run finishes so the UI directive
    appears. A FAILED outcome keeps pending_restart at whatever it was
    (typically False) — restarting with broken config doesn't help."""
    from app.settings.validation import ValidationState, get_validator

    validator = get_validator()
    status = await validator.validate()
    validator.set_pending_restart(status.state == ValidationState.PASSED)


def _emit_interrupt_ack(
    client_msg_id: Optional[str],
    ws_send_fn: Callable[..., None],
) -> None:
    """Emit ``control_command_acknowledged`` so the FE renders the
    cancelled message as a Directive pill (and drops its request-id
    badge). Shared by the keyword path and the arbiter ``interrupt_only``
    path so both cancel routes render identically — the distinction
    between them is preserved separately via ``interrupt_decision.decision_source``."""
    payload: dict[str, Any] = {
        "event_type": "control_command_acknowledged",
        "source": "[Session Control]",
        "action": "interrupt_only",
    }
    if client_msg_id is not None:
        payload["client_msg_id"] = client_msg_id
    ws_send_fn(CHANNEL_AGENT_OUTPUTS, payload)


def _handle_keyword_directive(
    body: str,
    client_msg_id: Optional[str],
    state: "WebsocketSessionState",
    ws_send_fn: Callable[..., None],
) -> bool:
    """Intercept an explicit interrupt keyword.

    Returns True when ``body`` matches
    :func:`app.runtime.cancellation.is_explicit_interrupt_message` —
    cancels the active task, emits ``control_command_acknowledged`` so
    the FE renders the outbound as a pill, and the caller must skip
    the regular chat dispatch. Returns False otherwise; the caller
    proceeds normally.
    """
    from app.runtime.cancellation import is_explicit_interrupt_message
    if not is_explicit_interrupt_message(body):
        return False
    if state.active_cancel_event is not None:
        state.active_cancel_event.set()
    _emit_interrupt_ack(client_msg_id, ws_send_fn)
    ws_send_fn(CHANNEL_LLM_DIAGNOSTICS, {
        "event_type": "interrupt_decision",
        "decision_source": "keyword",
        "action": "interrupt_only",
        "reason": f"Keyword directive matched: {body!r}",
        "incoming_request": body,
        "timestamp_ms": int(time.time() * 1000),
    })
    return True


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A user chat body paired with the frontend's outbound id.

    The id rides through the queue and the request flow so
    ``request_id_assignment`` can echo it back for badge correlation.
    """
    body: str
    client_msg_id: Optional[str] = None


@dataclass
class WebsocketSessionState:
    active_task: Optional[asyncio.Task[None]] = None
    active_message: Optional[ChatMessage] = None
    active_cancel_event: Optional[threading.Event] = None
    pending_messages: Deque[ChatMessage] = field(
        default_factory=lambda: deque(maxlen=PENDING_MESSAGES_MAXLEN),
    )


async def _apply_arbiter_decision(
    state: "WebsocketSessionState",
    message: ChatMessage,
    decision: Any,
    start_next_if_idle: Callable[[], Awaitable[None]],
    ws_send_fn: Optional[Callable[..., None]] = None,
) -> None:
    """Apply an arbiter decision to ``state`` and ensure pending
    messages drain.

    ``interrupt_only`` cancels the active task and is acknowledged as a
    control directive (so the FE renders it as a pill, like a keyword
    cancel — it never becomes a request). ``interrupt_and_replace``
    cancels and front-queues. The default (``queue``) back-queues.
    All non-``interrupt_only`` paths call ``start_next_if_idle`` so a
    queued message processes even when the active task completed
    during the arbiter call (the active task's ``_runner.finally``
    drain runs at most once and may have already seen an empty queue).
    """
    if decision.action == "interrupt_only":
        if state.active_cancel_event:
            state.active_cancel_event.set()
        if ws_send_fn is not None:
            _emit_interrupt_ack(message.client_msg_id, ws_send_fn)
        return

    if decision.action == "interrupt_and_replace":
        if state.active_cancel_event:
            state.active_cancel_event.set()
        state.pending_messages.appendleft(message)
    else:
        state.pending_messages.append(message)

    await start_next_if_idle()


async def _ws_send_to_client(
    websocket: ServerConnection,
    channel: str,
    payload: Any,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    message: dict[str, Any] = {"channel": channel, "payload": payload}
    if meta:
        message["meta"] = meta
    await websocket.send(json.dumps(message))


async def _handle_json_request(
    websocket: ServerConnection,
    body: str,
    response_channel: str,
    handler: Callable[[dict], Awaitable[dict]],
) -> dict:
    """Shared try/parse/handle/send template for request-channel handlers.

    Centralises the boilerplate shared by every request channel:

    - Parse body as JSON and extract ``request_id`` (so errors still
      correlate).
    - Await *handler(payload)*; spread its return dict into the
      response verbatim — handlers own their success shape
      (``ok: true`` vs ``accepted: true``).
    - On any exception, send ``{request_id, ok: false, error: <str>}``.
    - Return the sent payload so callers that need a post-hook
      (e.g. firing a background task on success) can inspect it
      without re-parsing.
    """
    request_id = None
    try:
        payload = json.loads(body)
        request_id = payload.get("request_id")
        result = await handler(payload)
        response_payload: dict[str, Any] = {"request_id": request_id, **result}
    except Exception as exc:  # noqa: BLE001
        response_payload = {
            "request_id": request_id,
            "ok": False,
            "error": redact_secrets(str(exc)),
        }
    await _ws_send_to_client(websocket, response_channel, response_payload)
    return response_payload


async def _background_scan(
    websocket: ServerConnection,
    logs_root: Path,
    request_id: Optional[str],
) -> None:
    """Run scan_and_analyse off the WebSocket receive loop.

    Sends a `completed: true` follow-up on the same analysis-run-response
    channel and a `list_refreshed` update event. Swallows errors so a
    failure never kills the receive loop.
    """
    _log_bg = logging.getLogger("swarpius.ws.analysis_scan")
    try:
        loop = asyncio.get_running_loop()
        from app.analysis.browser import list_analysed_conversations, scan_and_analyse

        result = await loop.run_in_executor(None, scan_and_analyse, logs_root)
        _log_bg.info("Analysis scan complete: %s", result)
        await _ws_send_to_client(
            websocket,
            CHANNEL_ANALYSIS_RUN_RESPONSE,
            {"request_id": request_id, "completed": True, **result},
        )
        if result.get("ok"):
            list_result = await loop.run_in_executor(
                None, lambda: list_analysed_conversations(logs_root),
            )
            await _ws_send_to_client(websocket, CHANNEL_ANALYSIS_UPDATE, {
                "type": "list_refreshed",
                "conversations": list_result["conversations"],
                "models": list_result["models"],
            })
    except websockets.exceptions.ConnectionClosed:
        _log_bg.debug("Client disconnected before scan result could be sent")
    except Exception:
        _log_bg.warning("Background scan failed", exc_info=True)
        try:
            await _ws_send_to_client(
                websocket,
                CHANNEL_ANALYSIS_RUN_RESPONSE,
                {
                    "request_id": request_id,
                    "completed": True,
                    "ok": False,
                    "error": "Internal error during scan (see server log)",
                },
            )
        except Exception:
            # We're already in an error path trying to surface the
            # original failure — if the WS itself is gone the client
            # has disconnected, nothing more we can do.
            pass


async def _background_rerun(
    websocket: ServerConnection,
    logs_root: Path,
    date: str,
    conversation_id: str,
    request_id: Optional[str],
) -> None:
    """Run a single-conversation re-analysis off the WS receive loop.

    Sends a `completed: true` follow-up on analysis-run-response plus a
    `list_entry_updated` analysis-update event on success.
    """
    _log_bg = logging.getLogger("swarpius.ws.analysis_rerun")
    try:
        loop = asyncio.get_running_loop()
        from app.analysis.browser import get_list_entry, run_analysis

        result = await loop.run_in_executor(
            None, run_analysis, logs_root, date, conversation_id,
        )
        await _ws_send_to_client(
            websocket,
            CHANNEL_ANALYSIS_RUN_RESPONSE,
            {"request_id": request_id, "completed": True, **result},
        )
        if result.get("ok"):
            entry = await loop.run_in_executor(
                None, get_list_entry, logs_root, date, conversation_id,
            )
            if entry:
                await _ws_send_to_client(websocket, CHANNEL_ANALYSIS_UPDATE, {
                    "type": "list_entry_updated",
                    "entry": entry,
                })
    except websockets.exceptions.ConnectionClosed:
        _log_bg.debug("Client disconnected before rerun result could be sent")
    except Exception:
        _log_bg.warning("Background rerun failed", exc_info=True)
        try:
            await _ws_send_to_client(
                websocket,
                CHANNEL_ANALYSIS_RUN_RESPONSE,
                {
                    "request_id": request_id,
                    "completed": True,
                    "ok": False,
                    "error": "Internal error during rerun (see server log)",
                },
            )
        except Exception:
            # Already in an error path — if the WS is gone the client
            # has disconnected, nothing more we can do.
            pass


# ── Per-channel request handlers ────────────────────────────────────────
# Each handler takes the parsed payload and returns the response body
# (excluding request_id, which the dispatcher prepends). On error the
# handler raises; _handle_json_request converts that into the standard
# {ok: false, error: <str>} shape.


async def _handle_session_control(payload: dict, runtime: Any) -> dict:
    action = str(payload.get("action") or "").strip().lower()
    if action != "retry_now":
        raise UnsupportedActionError(f"Unsupported session control action '{action}'")
    runtime.rate_limit_override_event.set()
    return {"ok": True, "action": action}


async def _handle_clear_conversation(
    payload: dict, runtime: Any, state: "WebsocketSessionState",
) -> dict:
    """Delete the persisted conversation: transcript, the model's working
    memory, and the conversation's Roon references. Refused while a request
    is in flight so a clear can't race the commit that finalises it (the UI
    also disables the control during a request)."""
    _ = payload
    if state.active_task is not None:
        return {
            "ok": False,
            "reason": "A request is in progress — try again once it finishes.",
        }
    await asyncio.to_thread(runtime.clear_conversation_state)
    return {"ok": True}


async def _handle_image_request(
    payload: dict, runtime: Any, loop: asyncio.AbstractEventLoop,
) -> dict:
    image_key = validate_image_key(payload.get("image_key"))
    width = int(payload.get("width") or 400)
    height = int(payload.get("height") or 400)
    image_payload = await loop.run_in_executor(
        None, runtime.get_image_base64_payload, image_key, width, height,
    )
    return {"ok": True, **image_payload}


async def _handle_roon_control(
    payload: dict, runtime: Any, loop: asyncio.AbstractEventLoop,
) -> dict:
    control_result = await loop.run_in_executor(
        None, runtime.execute_roon_control, payload,
    )
    return {"ok": True, **control_result}


async def _handle_feature_verify(
    body: str, runtime: Any, loop: asyncio.AbstractEventLoop,
) -> None:
    """Run a coordinator init for the requested feature off the WS receive
    loop. Fire-and-forget — the result lands on CHANNEL_FEATURE_AVAILABILITY
    via the coordinator's own broadcast on any state flip, so there's no
    response channel. Bad payloads are swallowed (logged): the only
    consequence of a malformed verify-request is no broadcast, never a
    receive-loop teardown."""
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError):
        _log.warning("feature-verify-request: malformed body, ignoring")
        return
    if not isinstance(payload, dict):
        return
    feature = str(payload.get("feature") or "").strip()
    if feature == "stop_marker":
        await loop.run_in_executor(
            None, runtime.verify_stop_marker_availability,
        )


async def _handle_open_data_folder(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Open the stop-marker folder in the OS file manager. Fire-and-forget
    — no response channel. Refused server-side outside a desktop bundle
    (see ``folder_reveal.open_stop_marker_folder``), so a malformed or
    out-of-context request silently skips, never a receive-loop
    teardown. Ignores any payload: the target folder is fixed."""
    from app.io.folder_reveal import open_stop_marker_folder
    await loop.run_in_executor(None, open_stop_marker_folder)


async def _handle_analysis_list(
    payload: dict, loop: asyncio.AbstractEventLoop,
) -> dict:
    from app.analysis.browser import list_analysed_conversations
    date_from = payload.get("date_from") or None
    date_to = payload.get("date_to") or None
    model_filter = payload.get("model") or None
    list_result = await loop.run_in_executor(
        None,
        lambda: list_analysed_conversations(
            _analysis_logs_root(),
            date_from=date_from,
            date_to=date_to,
            model=model_filter,
        ),
    )
    return {
        "ok": True,
        "conversations": list_result["conversations"],
        "models": list_result["models"],
    }


async def _handle_analysis_detail(
    payload: dict, loop: asyncio.AbstractEventLoop,
) -> dict:
    from app.analysis.browser import get_analysis_detail
    date = validate_date(payload.get("date"))
    conversation_id = validate_conversation_id(payload.get("conversation_id"))
    detail = await loop.run_in_executor(
        None, get_analysis_detail, _analysis_logs_root(), date, conversation_id,
    )
    return {"ok": True, "analysis": detail}


async def _handle_analysis_run(
    payload: dict, websocket: ServerConnection,
) -> dict:
    """Fire an analysis scan or rerun on a background task. Returns
    ``{accepted: true}`` — no ``ok`` key — because the real outcome
    arrives later via the background task's ``completed: true``
    follow-up on the same channel."""
    request_id = payload.get("request_id")
    action = str(payload.get("action") or "").strip().lower()
    if action == "rerun":
        date = validate_date(payload.get("date"))
        conversation_id = validate_conversation_id(payload.get("conversation_id"))
        asyncio.create_task(_background_rerun(
            websocket, _analysis_logs_root(), date, conversation_id, request_id,
        ))
        return {"accepted": True}
    if action == "scan":
        _log.info("Analysis scan requested via UI")
        asyncio.create_task(_background_scan(
            websocket, _analysis_logs_root(), request_id,
        ))
        return {"accepted": True}
    raise ValueError(f"Unknown action '{action}', expected 'rerun' or 'scan'")


async def _handle_analysis_metrics(
    payload: dict, loop: asyncio.AbstractEventLoop,
) -> dict:
    from app.analysis.browser import get_metrics
    metrics = await loop.run_in_executor(
        None,
        get_metrics,
        _analysis_metrics_path(),
        payload.get("after"),
        payload.get("before"),
        payload.get("ref"),
        payload.get("model"),
    )
    return {"ok": True, **metrics}


async def _handle_analysis_logs(
    payload: dict, loop: asyncio.AbstractEventLoop,
) -> dict:
    from app.analysis.browser import get_request_logs
    date = validate_date(payload.get("date"))
    conversation_id = validate_conversation_id(payload.get("conversation_id"))
    rq_id = validate_request_id(payload.get("rq_id"))
    logs = await loop.run_in_executor(
        None, get_request_logs, _analysis_logs_root(), date, conversation_id, rq_id,
    )
    return {"ok": True, "logs": logs}


async def _handle_analysis_result_handle(
    payload: dict, loop: asyncio.AbstractEventLoop,
) -> dict:
    from app.analysis.browser import get_result_handle_data
    date = validate_date(payload.get("date"))
    conversation_id = validate_conversation_id(payload.get("conversation_id"))
    result_handle = str(payload.get("result_handle") or "").strip()
    if not result_handle:
        raise ValueError("result_handle is required")
    data = await loop.run_in_executor(
        None,
        get_result_handle_data,
        _analysis_logs_root(),
        date,
        conversation_id,
        result_handle,
    )
    return {"ok": True, "data": data}


async def _handle_analysis_feedback(
    payload: dict, loop: asyncio.AbstractEventLoop,
) -> dict:
    action = str(payload.get("action") or "").strip()
    date = validate_date(payload.get("date"))
    conversation_id = validate_conversation_id(payload.get("conversation_id"))
    if action == "submit":
        from app.analysis.feedback import submit_feedback
        # finding_request_id is the *finding's* request_id (cXX-NNNN
        # inside analysis.yaml). The envelope-level request_id is the
        # WS correlation ID — they must not collide on the same key.
        finding_request_id = validate_request_id(payload.get("finding_request_id"))
        failure_mode = str(payload.get("failure_mode") or "").strip()
        disposition = str(payload.get("disposition") or "").strip()
        rebuttal = str(payload.get("rebuttal") or "").strip()
        if not failure_mode:
            raise ValueError("failure_mode is required")
        result = await loop.run_in_executor(
            None,
            submit_feedback,
            _analysis_logs_root(),
            date,
            conversation_id,
            finding_request_id,
            failure_mode,
            disposition,
            rebuttal,
        )
        return result
    if action == "status":
        from app.analysis.feedback import get_feedback_status
        result = await loop.run_in_executor(
            None, get_feedback_status, _analysis_logs_root(), date, conversation_id,
        )
        return result
    if action == "cancel":
        from app.analysis.feedback import delete_feedback
        finding_request_id = validate_request_id(payload.get("finding_request_id"))
        failure_mode = str(payload.get("failure_mode") or "").strip()
        if not failure_mode:
            raise ValueError("failure_mode is required")
        result = await loop.run_in_executor(
            None,
            delete_feedback,
            _analysis_logs_root(),
            date,
            conversation_id,
            finding_request_id,
            failure_mode,
        )
        return result
    raise ValueError(f"Unknown feedback action: {action}")


# Single-session hygiene: only one active socket is allowed at a time.
# A new connection with the same session_id (browser reconnect after a
# network blip) replaces the old socket silently. A new connection with
# a different session_id (another tab/device) closes the old socket with
# CLOSE_CODE_SESSION_TAKEOVER so the displaced client can show a
# "taken over" overlay and stop auto-reconnecting.
#
# Single-session is what keeps two browsers from stepping on each
# other through the shared RuntimeState.
_active_session: Optional[tuple[str, ServerConnection]] = None
_active_session_lock = asyncio.Lock()


def _extract_session_id(websocket: ServerConnection) -> Optional[str]:
    """Pull ``session_id`` out of the WebSocket URL query string."""
    request = getattr(websocket, "request", None)
    if request is None:
        return None
    path = getattr(request, "path", "") or ""
    query = urlparse(path).query
    if not query:
        return None
    values = parse_qs(query).get("session_id")
    return values[0] if values else None


async def _register_session(
    websocket: ServerConnection,
    session_id: Optional[str],
) -> str:
    """Enforce single-session hygiene.

    Returns the session_id associated with this connection (generated
    server-side when the client didn't supply one — e.g. CLI/curl tests).
    Closes any previously-registered socket as a side effect.
    """
    global _active_session
    if not session_id:
        session_id = f"anon-{secrets.token_hex(4)}"
        _log.info(
            "WebSocket client did not supply session_id — assigned %s "
            "(old clients and direct tools behave this way; the provided "
            "frontend always sends one).",
            session_id,
        )

    displaced: Optional[tuple[str, ServerConnection]] = None
    async with _active_session_lock:
        if _active_session is not None:
            displaced = _active_session
        _active_session = (session_id, websocket)

    if displaced is not None:
        old_session_id, old_socket = displaced
        if old_session_id == session_id:
            # Same-session reconnect: leave the old socket alone. Either
            # it's already closing client-side (close frame in flight) or
            # it's a transient duplicate (StrictMode dev / brief race).
            # Closing it here triggers a client reconnect cascade — the
            # client's close handler can't tell "this socket was
            # superseded" from "the server kicked me out", so it
            # reconnects, server displaces again, and we loop. The
            # displaced socket cleans itself up via its own finally.
            _log.debug(
                "Same-session reconnect (session=%s) — leaving previous "
                "socket to clean up itself",
                session_id,
            )
        else:
            _log.info(
                "Closing previous WebSocket (session=%s, reason=session taken over)",
                old_session_id,
            )
            try:
                await old_socket.close(
                    code=CLOSE_CODE_SESSION_TAKEOVER,
                    reason="session taken over",
                )
            except Exception:
                _log.debug("Previous socket already closed", exc_info=True)
    return session_id


async def _clear_session_if_current(websocket: ServerConnection) -> None:
    """Remove *websocket* from the active slot if it still holds it.

    Prevents a legitimate takeover from being evicted when the displaced
    socket later finishes its cleanup and lands in the ``finally`` block.
    """
    global _active_session
    async with _active_session_lock:
        if _active_session is not None and _active_session[1] is websocket:
            _active_session = None


def _extract_body(msg: dict) -> str:
    """Coerce an inbound WS frame to a body string: an explicit ``body``,
    else a string ``payload``, else a JSON-encoded dict/list ``payload``.
    Returns ``""`` when none apply (the caller skips empty bodies)."""
    body = (msg.get("body") or "").strip()
    if body:
        return body
    payload = msg.get("payload")
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (dict, list)):
        return json.dumps(payload)
    return ""


async def websocket_handler(
    websocket: ServerConnection,
    runtime: Any,
    ws_clients: set[ServerConnection],
    process_request_fn: Callable[..., None],
    arbitrate_interrupt_fn: Callable[[str, str], Any],
    ws_send_fn: Callable[[str, Any], None],
    auto_shutdown: Optional[Any] = None,
) -> None:
    # Path-based dispatch: ``/tts`` goes to the F5-TTS WS↔TCP proxy;
    # everything else (chiefly ``/ws``) is the existing chat flow.
    # The chat path's session registration and runtime-init machinery
    # don't apply to TTS — the proxy is a pure byte bridge.
    request = getattr(websocket, "request", None)
    path = getattr(request, "path", "/ws") or "/ws"
    if path == "/tts" or path.startswith("/tts?"):
        from tts import proxy as tts_proxy
        await tts_proxy.handle(websocket)
        return

    # No ensure_initialised() here. In WS mode the background init thread
    # (agent.py) owns initialisation and broadcasts roon_state transitions
    # to ws_clients. Calling it on connect would block this client for the
    # whole Roon pairing wait (it contends on the same init lock), so the
    # feature-availability / settings-read the browser needs to render the
    # setup view would never arrive until pairing finished.
    session_id = _extract_session_id(websocket)
    session_id = await _register_session(websocket, session_id)
    ws_clients.add(websocket)
    if auto_shutdown is not None:
        auto_shutdown.on_connect()
    remote = websocket.remote_address
    _log.info(
        "WebSocket client connected: %s (session=%s)",
        f"{remote[0]}:{remote[1]}" if remote else "unknown",
        session_id,
    )
    state = WebsocketSessionState()
    id_generator = RequestIdGenerator()
    loop = asyncio.get_running_loop()

    async def _start_next_if_idle() -> None:
        if state.active_task is not None:
            return
        if not state.pending_messages:
            return

        next_message = state.pending_messages.popleft()
        cancel_event = threading.Event()
        state.active_message = next_message
        state.active_cancel_event = cancel_event

        async def _runner() -> None:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: process_request_fn(
                        next_message.body,
                        cancel_event,
                        id_generator,
                        client_msg_id=next_message.client_msg_id,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                import traceback
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                _log.error("Unhandled error processing request:\n%s", redact_secrets(tb))
                try:
                    await _ws_send_to_client(
                        websocket,
                        CHANNEL_ERRORS,
                        {
                            "source": "[System]",
                            "error": f"Unhandled request error: {redact_secrets(str(exc))}",
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
            finally:
                state.active_task = None
                state.active_message = None
                state.active_cancel_event = None
                await _start_next_if_idle()

        state.active_task = asyncio.create_task(_runner())

    try:
        # feature-availability before zone-snapshots: the FE filter needs
        # the stop-marker title to rewrite marker-stuck zones to stopped,
        # and Roon can leave a zone stuck on the marker indefinitely.
        feature_availability_payload = await loop.run_in_executor(
            None, runtime.get_feature_availability_payload,
        )
        await _ws_send_to_client(
            websocket, CHANNEL_FEATURE_AVAILABILITY, feature_availability_payload,
        )

        initial_snapshot = await loop.run_in_executor(None, runtime.get_initial_zone_snapshot)
        await _ws_send_to_client(websocket, CHANNEL_ZONE_SNAPSHOTS, initial_snapshot)

        # Report Core health only once paired: a client joining during a
        # genuine mid-session outage then sees the "Reconnecting" overlay
        # immediately. Before pairing the RoonSetup view owns the screen,
        # so emitting "lost" here would flash that overlay on first-run /
        # restart.
        core_status = runtime.roon_core_status_for_connect()
        if core_status is not None:
            await _ws_send_to_client(
                websocket, CHANNEL_ROON_CORE_STATUS, {"state": core_status},
            )

        default_zone_payload = await loop.run_in_executor(None, runtime.get_default_zone_payload)
        await _ws_send_to_client(websocket, CHANNEL_DEFAULT_ZONE_UPDATE, default_zone_payload)

        # Validation status snapshot — UI uses this to render per-row
        # chips immediately on connect instead of waiting for the next
        # state transition (which may not come if the validator already
        # settled before the client arrived).
        from app.constants import CHANNEL_VALIDATION_STATUS
        from app.settings.validation import get_validator
        await _ws_send_to_client(
            websocket,
            CHANNEL_VALIDATION_STATUS,
            get_validator().current().to_dict(),
        )

        initial_queue_events = await loop.run_in_executor(None, runtime.get_initial_queue_events)
        for queue_event in initial_queue_events:
            await _ws_send_to_client(websocket, CHANNEL_QUEUE_UPDATES, queue_event)

        # Replay persisted messages — tag any from before this server session.
        # Bound the eager window so a long-lived persistent store doesn't dump
        # days of chat + diagnostics into every fresh connection. Older entries
        # stay on disk and are lazy-loaded on scroll-back.
        from agent import get_server_start_ms
        from app.constants import REPLAY_HISTORY_MS
        from app.io.message_store import get_message_store
        server_start_ms = get_server_start_ms()
        replay_cutoff_ms = int(time.time() * 1000) - REPLAY_HISTORY_MS

        for msg in get_message_store().get_all(since_ms=replay_cutoff_ms):
            replay_meta = dict(msg.get("meta") or {})
            replay_meta["replay"] = True
            replay_meta["created_at"] = msg.get("created_at")
            if msg.get("created_at", 0) < server_start_ms:
                replay_meta["previous_session"] = True
            await _ws_send_to_client(
                websocket,
                msg["channel"],
                msg["payload"],
                meta=replay_meta,
            )

        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            channel = msg.get("channel", CHANNEL_CHAT)
            body = _extract_body(msg)
            if not body:
                continue
            raw_client_msg_id = msg.get("client_msg_id")
            client_msg_id: Optional[str] = (
                raw_client_msg_id if isinstance(raw_client_msg_id, str) and raw_client_msg_id else None
            )

            # User chat is persisted at request completion (request_flow's
            # _persist_user_chat), grouped with that request — so a restart
            # dropping the in-flight request leaves no orphaned message.

            if channel == CHANNEL_SESSION_CONTROL_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_SESSION_CONTROL_RESPONSE,
                    lambda p: _handle_session_control(p, runtime),
                )
                continue
            if channel == CHANNEL_CLEAR_CONVERSATION_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_CLEAR_CONVERSATION_RESPONSE,
                    lambda p: _handle_clear_conversation(p, runtime, state),
                )
                continue
            if channel == CHANNEL_IMAGE_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_IMAGE_RESPONSE,
                    lambda p: _handle_image_request(p, runtime, loop),
                )
                continue
            if channel == CHANNEL_ROON_CONTROL_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_ROON_CONTROL_RESPONSE,
                    lambda p: _handle_roon_control(p, runtime, loop),
                )
                continue
            if channel == CHANNEL_ROON_EXPLORER_REQUEST:
                from app.settings import get_settings
                if not get_settings().enable_roon_explorer:
                    continue
                from app.io.explorer_flow import handle_explorer
                await _handle_json_request(
                    websocket, body, CHANNEL_ROON_EXPLORER_RESPONSE,
                    lambda p: handle_explorer(p, runtime, loop),
                )
                continue
            if channel == CHANNEL_FEATURE_VERIFY_REQUEST:
                await _handle_feature_verify(body, runtime, loop)
            if channel == CHANNEL_OPEN_DATA_FOLDER_REQUEST:
                await _handle_open_data_folder(loop)
                continue
            if channel == CHANNEL_ANALYSIS_LIST_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_LIST_RESPONSE,
                    lambda p: _handle_analysis_list(p, loop),
                )
                continue
            if channel == CHANNEL_ANALYSIS_DETAIL_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_DETAIL_RESPONSE,
                    lambda p: _handle_analysis_detail(p, loop),
                )
                continue
            if channel == CHANNEL_ANALYSIS_RUN_REQUEST:
                # Background task is kicked off inside _handle_analysis_run
                # and sends its `completed: true` follow-up on this same
                # channel when done. The synchronous reply is
                # {accepted: true} (no `ok` key) so the frontend can tell
                # the request was accepted vs already-completed.
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_RUN_RESPONSE,
                    lambda p: _handle_analysis_run(p, websocket),
                )
                continue
            if channel == CHANNEL_ANALYSIS_METRICS_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_METRICS_RESPONSE,
                    lambda p: _handle_analysis_metrics(p, loop),
                )
                continue
            if channel == CHANNEL_ANALYSIS_REQUEST_LOGS_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_REQUEST_LOGS_RESPONSE,
                    lambda p: _handle_analysis_logs(p, loop),
                )
                continue
            if channel == CHANNEL_ANALYSIS_RESULT_HANDLE_REQUEST:
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_RESULT_HANDLE_RESPONSE,
                    lambda p: _handle_analysis_result_handle(p, loop),
                )
                continue
            if channel == CHANNEL_ANALYSIS_FEEDBACK_REQUEST:
                # Feedback submit does not trigger immediate re-analysis
                # — rapid back-to-back submits would otherwise race,
                # producing duplicate history entries and silently
                # dropped disputes. The scheduled analyser picks up
                # pending feedback on its next tick; the operator can
                # force an immediate pass via the Re-Analyse button.
                await _handle_json_request(
                    websocket, body, CHANNEL_ANALYSIS_FEEDBACK_RESPONSE,
                    lambda p: _handle_analysis_feedback(p, loop),
                )
                continue
            if channel == CHANNEL_SETTINGS_READ_REQUEST:
                from app.settings.endpoints import handle_read as _settings_read
                await _handle_json_request(
                    websocket, body, CHANNEL_SETTINGS_READ_RESPONSE,
                    lambda p: asyncio.to_thread(_settings_read, p),
                )
                continue
            if channel == CHANNEL_SETTINGS_SAVE_REQUEST:
                from app.settings.endpoints import handle_save as _settings_save
                response = await _handle_json_request(
                    websocket, body, CHANNEL_SETTINGS_SAVE_RESPONSE,
                    lambda p: asyncio.to_thread(_settings_save, p),
                )
                # After a successful save the config-missing state may
                # have flipped — re-broadcast feature-availability so
                # the frontend transitions to / from the Settings page
                # as appropriate.
                runtime._broadcast_feature_availability()
                # Save & Validate: every successful save re-runs live
                # validation so the user sees per-row results. PASSED
                # also flips pending_restart so the directive message
                # appears.
                if response.get("ok"):
                    asyncio.create_task(_revalidate_after_save())
                # Handle the "Restart" path: if the request
                # asked for a restart and the save succeeded, schedule
                # a clean shutdown shortly after the response was sent.
                # Docker auto-restarts via its compose policy; bundle
                # users see a "please relaunch" overlay; native python3
                # users see the process exit (deliberate).
                if response.get("ok") and _save_request_wants_restart(body):
                    from app.runtime.restart_signal import request_restart
                    request_restart()
                    callback = getattr(runtime, "signal_shutdown", None)
                    if callable(callback):
                        # 2-second grace so the response + the next
                        # feature-availability broadcast both flush.
                        loop.call_later(2.0, callback)
                continue
            if channel == CHANNEL_SETTINGS_RELOAD_REQUEST:
                from app.settings.endpoints import handle_reload as _settings_reload
                await _handle_json_request(
                    websocket, body, CHANNEL_SETTINGS_RELOAD_RESPONSE,
                    lambda p: asyncio.to_thread(_settings_reload, p),
                )
                runtime._broadcast_feature_availability()
                continue
            if channel == CHANNEL_SETTINGS_TEST_REQUEST:
                from app.settings.test_endpoint import (
                    handle_test_and_persist as _settings_test,
                )
                await _handle_json_request(
                    websocket, body, CHANNEL_SETTINGS_TEST_RESPONSE,
                    lambda p: asyncio.to_thread(_settings_test, p),
                )
                continue
            if channel != CHANNEL_CHAT:
                continue

            # Only gate keyword catches when something's in flight —
            # with nothing active, "cancel" has no target and should reach the LLM.
            if state.active_task is not None and _handle_keyword_directive(
                body=body, client_msg_id=client_msg_id,
                state=state, ws_send_fn=ws_send_fn,
            ):
                continue

            chat_message = ChatMessage(body=body, client_msg_id=client_msg_id)

            if state.active_task is None:
                state.pending_messages.append(chat_message)
                await _start_next_if_idle()
                continue

            active_body = state.active_message.body if state.active_message else ""
            arbiter_executor = getattr(runtime, "arbiter_executor", None)
            decision = await loop.run_in_executor(
                arbiter_executor,
                arbitrate_interrupt_fn,
                active_body,
                body,
            )

            decision_note = (
                f"Interrupt arbiter decision: action={decision.action}, "
                f"confidence={decision.confidence:.2f}, reason={decision.reason}"
            )
            ws_send_fn(
                CHANNEL_AGENT_OUTPUTS,
                {
                    "source": "[Session Control]",
                    "text": decision_note,
                },
            )

            decision_source = (
                "arbiter_fallback"
                if (decision.confidence == 0.0 and "Arbiter failed" in (decision.reason or ""))
                else "arbiter"
            )
            ws_send_fn(
                CHANNEL_LLM_DIAGNOSTICS,
                {
                    "event_type": "interrupt_decision",
                    "decision_source": decision_source,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "active_request": active_body,
                    "incoming_request": body,
                    "timestamp_ms": int(time.time() * 1000),
                },
            )

            await _apply_arbiter_decision(
                state, chat_message, decision, _start_next_if_idle,
                ws_send_fn=ws_send_fn,
            )
    except Exception as exc:
        if isinstance(exc, websockets.exceptions.ConnectionClosed):
            _log.info("WebSocket client disconnected: %s", f"{remote[0]}:{remote[1]}" if remote else "unknown")
        else:
            _log.exception("WebSocket handler error: %s", exc)
    finally:
        if state.active_cancel_event:
            state.active_cancel_event.set()
        if state.active_task:
            try:
                await state.active_task
            except Exception:
                _log.debug("Active task exception during cleanup", exc_info=True)
        ws_clients.discard(websocket)
        if auto_shutdown is not None:
            auto_shutdown.on_disconnect()
        await _clear_session_if_current(websocket)
