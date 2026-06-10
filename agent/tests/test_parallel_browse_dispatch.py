"""Tests for the patched on_message dispatch in parallel_browse.

Pins the two paths that must not leak a Future:

  1. Happy path — well-formed response resolves the Future with the
     decoded JSON body, and the original on_message is NOT called for
     that message.
  2. Malformed-body path — if json.loads on the body raises *after* we
     have popped the Future from ``pending``, the patched handler must
     ``set_exception`` on the Future so the caller's
     ``future.result(timeout=...)`` returns a None (treated as a clean
     timeout) instead of waiting until the real timeout fires.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.parallel_browse import install  # noqa: E402


class _FakeInnerSocket:
    """Stand-in for the websocket-client socket the lib stores at sock._socket."""
    def __init__(self):
        self.on_message = None


class _FakeSocket:
    """Stand-in for python-roonapi's RoonApiWebSocket wrapper.

    Tests pre-queue a response for the next request via
    ``queue_next_response``. When ``send_request`` mints that
    request_id, it spawns a thread that calls ``on_message`` with the
    queued frame. The thread blocks on ``parallel_browse``'s internal
    lock — which ``_send_and_wait`` is holding while it calls
    ``send_request`` — so the response can't be delivered until the
    Future is registered. No timing-based delay needed.
    """

    def __init__(self):
        self.connected = True
        self.on_message = lambda *args, **kw: None  # original handler
        self._socket = _FakeInnerSocket()
        self._next_id = 1
        self.sent = []  # (command, data) tuples in send order
        self._queued_responses: dict[int, str] = {}

    def queue_next_response(self, frame_text):
        """Queue *frame_text* to be delivered as the response for the
        next ``send_request`` call."""
        self._queued_responses[self._next_id] = frame_text

    def send_request(self, command, data):
        rid = self._next_id
        self._next_id += 1
        self.sent.append((command, data))
        frame = self._queued_responses.pop(rid, None)
        if frame is not None:
            threading.Thread(
                target=lambda: self.on_message(self, frame),
                daemon=True,
            ).start()
        return rid


class _FakeApi:
    def __init__(self):
        self._roonsocket = _FakeSocket()


def _make_response(request_id: int, body: str) -> str:
    """Compose a Roon-style websocket frame with Request-Id header + body."""
    return (
        f"COMPLETE Success\n"
        f"Request-Id: {request_id}\n"
        f"Content-Type: application/json\n"
        f"\n"
        f"{body}"
    )


def _queue_response(api, frame_text):
    """Pre-queue a response that the fake will deliver when its next
    ``send_request`` is called. ``send_request`` runs while
    ``parallel_browse``'s lock is held, so the spawned response thread
    blocks on that lock until the Future is registered — deterministic
    ordering, no Timer needed."""
    api._roonsocket.queue_next_response(frame_text)


class TestParallelBrowseDispatch(unittest.TestCase):
    def test_happy_path_resolves_future_with_decoded_body(self):
        api = _FakeApi()
        install(api)

        _queue_response(api, _make_response(1, '{"action": "list"}'))
        result = api.browse_browse({"hierarchy": "browse"})

        self.assertEqual(result, {"action": "list"})

    def test_malformed_body_does_not_leak_future_until_timeout(self):
        api = _FakeApi()
        install(api)

        # Body claims JSON ("{") but is unparseable. Without the leak
        # guard, the future would be popped from ``pending`` but never
        # resolved → caller would block until the real timeout. With the
        # guard, set_exception fires and the caller returns None quickly.
        _queue_response(api, _make_response(1, "{not valid json"))

        # If the guard doesn't fire, this would block for ~30s; cap the
        # wall-clock with a wide-but-finite expectation.
        started = time.monotonic()
        result = api.browse_browse({"hierarchy": "browse"})
        elapsed = time.monotonic() - started

        self.assertIsNone(result)
        self.assertLess(elapsed, 2.0, f"Expected fast failure, took {elapsed:.2f}s")

    def test_response_without_matching_request_id_falls_through(self):
        api = _FakeApi()
        captured = []
        # Replace original handler so we can assert it sees stray messages.
        api._roonsocket.on_message = lambda sock, msg: captured.append(msg)

        install(api)

        # Send a response with a request_id we never registered.
        api._roonsocket.on_message(api._roonsocket, _make_response(999, '{"x": 1}'))

        self.assertEqual(len(captured), 1)

    def test_late_response_after_timeout_logs_warning(self):
        """Smoke-test diagnostic: when Roon sends a response *after* we've
        given up waiting, the patched handler logs how late it arrived so
        operators can tell 'Roon dropped it' from 'Roon was just slow'."""
        api = _FakeApi()
        # Tight timeout so the test doesn't have to wait 30s.
        with patch("roon_core.parallel_browse._REQUEST_TIMEOUT", 0.1):
            install(api)

            # Fire the request without scheduling a response — let it
            # time out.
            result = api.browse_browse({"hierarchy": "browse"})
            self.assertIsNone(result)

            # Now deliver the response, late.
            time.sleep(0.05)  # simulate the "real" delay
            with self.assertLogs("swarpius.parallel_browse", level="WARNING") as cm:
                api._roonsocket.on_message(
                    api._roonsocket, _make_response(1, '{"action": "list"}'),
                )

        late_messages = [m for m in cm.output if "late response" in m]
        self.assertEqual(len(late_messages), 1, cm.output)
        self.assertIn("request 1", late_messages[0])
        self.assertIn("after timeout", late_messages[0])


if __name__ == "__main__":
    unittest.main()
