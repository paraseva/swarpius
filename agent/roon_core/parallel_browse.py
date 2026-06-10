"""Replace python-roonapi's browse_browse/browse_load with thread-safe
Future-based request-response correlation.

The stock ``_request()`` method polls a shared ``_results`` dict with
50ms sleeps and no locking.  Under concurrent calls from different
threads, responses can be consumed by the wrong caller — browse_load
intermittently returns ``None``.

This module monkey-patches the ``RoonApi`` instance so that each
browse request registers a ``Future`` keyed by ``request_id``.  A
patched ``on_message`` handler resolves the Future when the matching
response arrives on the websocket thread.  Callers block on their
own Future — no polling, no cross-thread interference.

The socket is accessed dynamically via ``api._roonsocket`` on every
call, so the patch survives library-level websocket reconnections.
"""

import json
import logging
import threading
import time
from concurrent.futures import Future

_log = logging.getLogger("swarpius.parallel_browse")

SERVICE_BROWSE = "com.roonlabs.browse:1"
_REQUEST_TIMEOUT = 30  # seconds
# Track recently-timed-out request_ids so we can log a warning if Roon
# sends the response after we gave up. Lets us tell "Roon dropped it"
# from "Roon was just slow" — the latter would show up here as a late
# arrival with elapsed > 0.
_LATE_RESPONSE_TTL = 60.0  # seconds


def install(api):
    """Patch *api*.browse_browse and *api*.browse_load with Future-based
    dispatch.  Safe to call once per ``RoonApi`` instance (idempotent)."""

    if getattr(api, "_parallel_browse_installed", False):
        return

    if not hasattr(api, "_roonsocket"):
        return

    api._parallel_browse_installed = True

    pending = {}
    recently_timed_out: dict[int, float] = {}
    lock = threading.Lock()
    patched_socket_ids = set()

    def _record_timeout_locked(request_id: int) -> None:
        """Mark request_id as recently timed out and prune stale entries.
        Caller must hold the lock."""
        now = time.monotonic()
        recently_timed_out[request_id] = now
        cutoff = now - _LATE_RESPONSE_TTL
        for rid in [r for r, t in recently_timed_out.items() if t < cutoff]:
            recently_timed_out.pop(rid, None)

    def _ensure_socket_patched():
        """Patch on_message on the current socket if not already done.

        After a reconnect the library replaces ``api._roonsocket`` with a
        fresh ``RoonApiWebSocket``.  We detect this by object id and
        re-apply the on_message hook so Future dispatch keeps working.
        """
        sock = api._roonsocket
        if sock is None:
            return None

        sid = id(sock)
        if sid in patched_socket_ids:
            return sock

        original_on_message = sock.on_message

        def _patched_on_message(w_socket, message=None):
            if not message:
                message = w_socket
            try:
                raw = message.decode("utf-8") if isinstance(message, bytes) else message
                request_id = None
                for line in raw.split("\n"):
                    if line.startswith("Request-Id: "):
                        request_id = int(line.split("Request-Id: ")[1])
                        break

                if request_id is not None:
                    with lock:
                        future = pending.pop(request_id, None)
                        timed_out_at = recently_timed_out.pop(request_id, None)
                    if future is not None:
                        try:
                            body = ""
                            if "Content-Type:" in raw:
                                body = "".join(raw.split("\n\n")[1:])
                            if body and "{" in body:
                                body = json.loads(body)
                            future.set_result(body)
                        except Exception as exc:
                            # Body decode failed *after* we claimed the
                            # request_id — surface it to the caller so it
                            # doesn't sit waiting until timeout. The caller
                            # raises ExternalServiceError in that case.
                            future.set_exception(exc)
                        return None
                    if timed_out_at is not None:
                        # Late arrival: Roon did send a response, but only
                        # after our deadline. Lets us distinguish "Roon
                        # dropped it" from "Roon was just slow" empirically.
                        elapsed = time.monotonic() - timed_out_at
                        _log.warning(
                            "late response for request %d (arrived %.1fs after timeout)",
                            request_id, elapsed,
                        )
                        return None
            except Exception:
                _log.debug("parallel_browse on_message parse error", exc_info=True)

            # on_message handlers don't have a meaningful return value;
            # chain to the original for completeness and discard the result.
            original_on_message(w_socket, message)
            return None

        sock.on_message = _patched_on_message
        sock._socket.on_message = _patched_on_message
        patched_socket_ids.add(sid)
        _log.info("Parallel browse dispatch active (socket %d)", sid)
        return sock

    def _send_and_wait(command, data):
        sock = _ensure_socket_patched()
        if sock is None or not sock.connected:
            return None
        future = Future()
        with lock:
            request_id = sock.send_request(command, data)
            if request_id is False:
                return None
            pending[request_id] = future
        try:
            return future.result(timeout=_REQUEST_TIMEOUT)
        except TimeoutError:
            with lock:
                pending.pop(request_id, None)
                _record_timeout_locked(request_id)
            _log.warning("browse request %d timed out after %ds",
                         request_id, _REQUEST_TIMEOUT)
            return None
        except Exception as exc:
            # set_exception() from the on_message handler — body
            # decode failed for a response we routed to this future.
            with lock:
                pending.pop(request_id, None)
            _log.warning("browse request %d failed to decode: %s",
                         request_id, exc)
            return None

    def browse_browse(opts):
        return _send_and_wait(f"{SERVICE_BROWSE}/browse", opts)

    def browse_load(opts):
        return _send_and_wait(f"{SERVICE_BROWSE}/load", opts)

    api.browse_browse = browse_browse
    api.browse_load = browse_load

    _ensure_socket_patched()
