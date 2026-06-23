"""websocket_handler receive-loop routing, chat persistence, and the
save-restart hook — driven through the handler with a fake WS that
async-iterates crafted frames and a stubbed runtime (deps are injected, so
process_request / broadcast / runtime are supplied as test doubles).

Contract: a settings-test frame routes to the test-response channel; a
chat frame is NOT persisted on receipt (persistence is deferred to request
completion so a dropped in-flight request leaves no orphan); a successful
save that asked to restart re-broadcasts feature-availability and requests
a restart.
"""
import asyncio
import importlib
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Force agent.py's module body to run now (imported for its side effect, hence
# no bound name): it calls set_message_store(SqliteMessageStore), which the
# connect-burst's lazy `from agent import ...` would otherwise trigger
# mid-handler and clobber the capturing store a test sets below.
importlib.import_module("agent")
from app.constants import (  # noqa: E402
    CHANNEL_CHAT,
    CHANNEL_SETTINGS_SAVE_REQUEST,
    CHANNEL_SETTINGS_TEST_REQUEST,
    CHANNEL_SETTINGS_TEST_RESPONSE,
)
from app.io.message_store import (  # noqa: E402
    MessageStore,
    NullMessageStore,
    set_message_store,
)
from app.io.websocket_flow import websocket_handler  # noqa: E402
from app.settings.validation import reset_validator_for_tests  # noqa: E402


class _CapturingStore(MessageStore):
    def __init__(self):
        self.appended = []

    def clear(self):
        pass

    def append(self, channel, payload, meta=None):
        self.appended.append((channel, payload, meta))

    def get_all(self, since_ms=None):
        return []

    def load_day(self, before_ms, channel=None):
        return {"messages": [], "has_older": False}

    def load_range(self, start_ms, end_ms, channel=None):
        return {"messages": [], "has_older": False}

    def close(self):
        pass


class _FakeWS:
    def __init__(self, frames):
        self._frames = [f if isinstance(f, str) else json.dumps(f) for f in frames]
        self.request = SimpleNamespace(path="/ws")
        self.remote_address = ("127.0.0.1", 5000)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def close(self, *a, **k):
        pass

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for frame in self._frames:
            yield frame


async def _noop_async(*a, **k):
    pass


def _make_runtime():
    rt = MagicMock()
    rt.get_feature_availability_payload.return_value = {}
    rt.get_initial_zone_snapshot.return_value = {}
    rt.roon_core_status_for_connect.return_value = None
    rt.get_default_zone_payload.return_value = {}
    rt.get_initial_queue_events.return_value = []
    return rt


def _run_handler(frames, runtime):
    ws = _FakeWS(frames)
    process_request_fn = MagicMock()
    asyncio.run(websocket_handler(
        ws, runtime, set(), process_request_fn, MagicMock(), MagicMock(), None,
    ))
    return ws, process_request_fn


def _sent_channels(ws):
    return [json.loads(s).get("channel") for s in ws.sent]


class TestWebsocketReceiveLoop(unittest.TestCase):
    def setUp(self):
        reset_validator_for_tests()
        set_message_store(NullMessageStore())

    def tearDown(self):
        set_message_store(NullMessageStore())
        reset_validator_for_tests()

    def test_settings_test_frame_routes_to_test_response(self):
        with patch(
            "app.settings.test_endpoint.handle_test_and_persist",
            return_value={"ok": True, "provider": "tts"},
        ):
            ws, _ = _run_handler([
                {"channel": CHANNEL_SETTINGS_TEST_REQUEST,
                 "payload": {"provider": "tts", "request_id": "rq-1"}},
            ], _make_runtime())
        self.assertIn(CHANNEL_SETTINGS_TEST_RESPONSE, _sent_channels(ws))

    def test_chat_frame_not_persisted_on_receipt(self):
        # Persistence is deferred to request completion (request_flow's
        # _persist_user_chat, grouped with the request) so a restart that
        # drops the in-flight request leaves no orphaned message. The loop
        # itself must not write the chat on receipt.
        store = _CapturingStore()
        set_message_store(store)
        _run_handler(
            [{"channel": CHANNEL_CHAT, "body": "hello", "client_msg_id": "cm1"}],
            _make_runtime(),
        )
        chat = [(c, p, m) for (c, p, m) in store.appended if c == CHANNEL_CHAT]
        self.assertEqual(chat, [], "user chat must not be persisted on receipt")

    def test_history_request_sends_day_messages_and_cursor(self):
        class _HistoryStore(_CapturingStore):
            def __init__(self, result):
                super().__init__()
                self._result = result
                self.requested = []

            def load_day(self, before_ms, channel=None):
                self.requested.append((before_ms, channel))
                return self._result

        result = {
            "messages": [{
                "id": 7, "channel": "chat",
                "payload": {"channel": "chat", "body": "hi"},
                "meta": None, "created_at": 1000,
            }],
            "has_older": True,
        }
        store = _HistoryStore(result)
        set_message_store(store)
        ws, _ = _run_handler(
            [{"channel": "history-request", "body": json.dumps({"before_ms": 5000})}],
            _make_runtime(),
        )
        self.assertIn((5000, None), store.requested)
        sent = [json.loads(s) for s in ws.sent]
        chat = [m for m in sent if m["channel"] == "chat"]
        cursor = [m for m in sent if m["channel"] == "history-cursor"]
        self.assertTrue(chat, "day's chat message not sent")
        self.assertEqual(chat[0]["meta"]["message_id"], 7)
        self.assertTrue(chat[0]["meta"]["historical"])
        self.assertTrue(cursor, "history-cursor not sent")
        self.assertTrue(cursor[-1]["payload"]["has_older"])

    def test_save_with_restart_rebroadcasts_and_requests_restart(self):
        runtime = _make_runtime()
        with patch("app.settings.endpoints.handle_save", return_value={"ok": True}), \
             patch("app.io.websocket_flow._save_request_wants_restart", return_value=True), \
             patch("app.io.websocket_flow._revalidate_after_save", new=_noop_async), \
             patch("app.runtime.restart_signal.request_restart") as req_restart:
            _run_handler([
                {"channel": CHANNEL_SETTINGS_SAVE_REQUEST,
                 "payload": {"restart": True, "request_id": "rq-1"}},
            ], runtime)
        runtime._broadcast_feature_availability.assert_called()
        req_restart.assert_called()


if __name__ == "__main__":
    unittest.main()
