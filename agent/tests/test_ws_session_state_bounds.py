"""Tests that WebsocketSessionState bounds resource use under flooding.

Without a cap the ``pending_messages`` deque could grow unboundedly
under a malicious or buggy client that floods the chat channel while
the active task is busy. The deque is bounded; once full, further
appends silently rotate (drop-oldest) so memory stays bounded.

The cap is exposed as a constant so it can be adjusted without
touching the dataclass default factory.
"""

from __future__ import annotations

import unittest

from app.constants import PENDING_MESSAGES_MAXLEN
from app.io.websocket_flow import ChatMessage, WebsocketSessionState


class TestPendingMessagesBound(unittest.TestCase):

    def test_default_factory_returns_bounded_deque(self):
        state = WebsocketSessionState()
        self.assertEqual(state.pending_messages.maxlen, PENDING_MESSAGES_MAXLEN)

    def test_overflow_drops_oldest(self):
        state = WebsocketSessionState()
        for i in range(PENDING_MESSAGES_MAXLEN + 5):
            state.pending_messages.append(ChatMessage(body=f"msg-{i}"))
        self.assertEqual(len(state.pending_messages), PENDING_MESSAGES_MAXLEN)
        # Oldest five should have rotated out.
        self.assertEqual(state.pending_messages[0].body, "msg-5")
        self.assertEqual(state.pending_messages[-1].body, f"msg-{PENDING_MESSAGES_MAXLEN + 4}")

    def test_constant_is_positive_integer(self):
        self.assertIsInstance(PENDING_MESSAGES_MAXLEN, int)
        self.assertGreater(PENDING_MESSAGES_MAXLEN, 0)
