"""Tests for the JSON request/response dispatcher used by websocket_handler.

The dispatcher wraps the try/parse/handle/send pattern that 9 WebSocket
request channels share. These tests pin the shape of the generated
responses so channel-specific refactors can lean on them.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from app.io.websocket_flow import _handle_json_request


class _FakeWebSocket:
    """Records outbound sends so assertions can inspect them."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


class TestHandleJsonRequest(unittest.IsolatedAsyncioTestCase):

    async def test_success_prepends_request_id_and_merges_handler_result(self):
        ws = _FakeWebSocket()

        async def handler(payload):
            return {"ok": True, "conversations": [{"id": "c01"}]}

        body = json.dumps({"request_id": "rq-1", "date_from": "2026-04-01"})

        await _handle_json_request(
            ws, body, "analysis-list-response", handler,
        )

        self.assertEqual(len(ws.sent), 1)
        msg = ws.sent[0]
        self.assertEqual(msg["channel"], "analysis-list-response")
        self.assertEqual(msg["payload"], {
            "request_id": "rq-1",
            "ok": True,
            "conversations": [{"id": "c01"}],
        })

    async def test_handler_passed_parsed_payload(self):
        """Handler receives the parsed dict so it can read request fields."""
        ws = _FakeWebSocket()
        seen = {}

        async def handler(payload):
            seen.update(payload)
            return {"ok": True}

        body = json.dumps({"request_id": "rq-2", "action": "foo", "count": 42})

        await _handle_json_request(ws, body, "x-response", handler)

        self.assertEqual(seen["action"], "foo")
        self.assertEqual(seen["count"], 42)

    async def test_handler_raises_sends_error_response_with_request_id(self):
        ws = _FakeWebSocket()

        async def handler(payload):
            raise ValueError("bad input")

        body = json.dumps({"request_id": "rq-3"})

        await _handle_json_request(ws, body, "x-response", handler)

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0]["payload"], {
            "request_id": "rq-3",
            "ok": False,
            "error": "bad input",
        })

    async def test_malformed_json_body_produces_error_with_no_request_id(self):
        """If the body isn't JSON, the handler never runs; response carries
        request_id=None so the frontend can still correlate the error
        (the message arrives on the expected response channel)."""
        ws = _FakeWebSocket()

        async def handler(payload):
            self.fail("Handler should not be called for malformed JSON")

        await _handle_json_request(ws, "not-json-{", "x-response", handler)

        self.assertEqual(len(ws.sent), 1)
        payload = ws.sent[0]["payload"]
        self.assertIsNone(payload["request_id"])
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    async def test_accepted_response_shape_preserved(self):
        """analysis-run-request uses {accepted: true} as the synchronous
        success marker (not {ok: true}). Dispatcher must spread the
        handler result verbatim — it does not impose an ok field."""
        ws = _FakeWebSocket()

        async def handler(payload):
            return {"accepted": True}

        body = json.dumps({"request_id": "rq-4", "action": "scan"})

        await _handle_json_request(
            ws, body, "analysis-run-response", handler,
        )

        payload = ws.sent[0]["payload"]
        self.assertEqual(payload, {"request_id": "rq-4", "accepted": True})
        self.assertNotIn("ok", payload)

    async def test_returns_response_payload_for_post_hooks(self):
        """Returns the payload sent so callers that need to check
        success (e.g. to kick off a background task) can do so without
        re-parsing."""
        ws = _FakeWebSocket()

        async def handler(payload):
            return {"ok": True, "lesson_status": "validated"}

        body = json.dumps({"request_id": "rq-5"})

        result = await _handle_json_request(ws, body, "x-response", handler)

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["lesson_status"], "validated")


class TestHandleFeatureVerify(unittest.IsolatedAsyncioTestCase):
    """Fire-and-forget handler for feature-verify-request.

    Raw WS message bodies arrive as JSON strings; the handler must
    parse before reading fields (a prior version called body.get()
    on the string and tore down the WS receive loop on every click).
    Result lands on feature-availability via the coordinator's own
    broadcast — no response channel here.
    """

    def _make_loop_runtime(self):
        from app.io.websocket_flow import _handle_feature_verify

        verify_calls: list[bool] = []

        class FakeRuntime:
            def verify_stop_marker_availability(self) -> bool:
                verify_calls.append(True)
                return True

        loop = asyncio.get_event_loop()
        return _handle_feature_verify, FakeRuntime(), loop, verify_calls

    async def test_well_formed_body_invokes_runtime_verify(self):
        handle, runtime, loop, calls = self._make_loop_runtime()
        body = json.dumps({"request_id": "rq-1", "feature": "stop_marker"})

        await handle(body, runtime, loop)

        self.assertEqual(calls, [True])

    async def test_malformed_json_body_does_not_raise(self):
        """An unparseable body must not raise out of the receive loop —
        a raised AttributeError would kill the WS connection on every
        verify-button click."""
        handle, runtime, loop, calls = self._make_loop_runtime()
        # Survives — no exception bubbles up — and runtime.verify
        # is not called for an unparseable body.
        await handle("not-json-{", runtime, loop)
        self.assertEqual(calls, [])

    async def test_unknown_feature_is_ignored(self):
        """A feature name we don't know about (or empty) is silently
        ignored, not a crash. Forward-compat for additional verify-able
        features without code changes here."""
        handle, runtime, loop, calls = self._make_loop_runtime()
        await handle(json.dumps({"feature": "nonexistent"}), runtime, loop)
        await handle(json.dumps({}), runtime, loop)
        self.assertEqual(calls, [])

    async def test_payload_missing_or_non_dict_is_ignored(self):
        """A JSON-array or JSON-string body parses successfully but
        isn't a dict — handler must not call .get() on it."""
        handle, runtime, loop, calls = self._make_loop_runtime()
        await handle("[1, 2, 3]", runtime, loop)
        await handle('"just a string"', runtime, loop)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
