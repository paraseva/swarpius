"""The clear-conversation WS handler: clears when idle, refuses while a
request is in flight (so a clear can't race the commit that finalises it).
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.io.websocket_flow import (
    _handle_clear_conversation,
    _handle_clear_listening_history,
)


class TestClearConversationHandler(unittest.TestCase):

    def test_clears_when_idle(self):
        runtime = MagicMock()
        state = SimpleNamespace(active_task=None)
        result = asyncio.run(_handle_clear_conversation({}, runtime, state))
        self.assertTrue(result["ok"])
        runtime.clear_conversation_state.assert_called_once()

    def test_refused_while_request_in_flight(self):
        runtime = MagicMock()
        state = SimpleNamespace(active_task=object())
        result = asyncio.run(_handle_clear_conversation({}, runtime, state))
        self.assertFalse(result["ok"])
        self.assertIn("reason", result)
        runtime.clear_conversation_state.assert_not_called()


class TestClearListeningHistoryHandler(unittest.TestCase):

    def test_clears_the_store(self):
        store = MagicMock()
        runtime = SimpleNamespace(listening_history=store)
        result = asyncio.run(_handle_clear_listening_history({}, runtime))
        self.assertTrue(result["ok"])
        store.clear.assert_called_once()

    def test_ok_when_no_store(self):
        runtime = SimpleNamespace(listening_history=None)
        result = asyncio.run(_handle_clear_listening_history({}, runtime))
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
