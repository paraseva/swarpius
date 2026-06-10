"""Tests for RequestIdGenerator sequence numbering across conversations."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.runtime.conversation_tracker import ConversationTracker
from app.runtime.request_logger import RequestIdGenerator


class MockClock:
    def __init__(self, start: float = 1000.0):
        self._time = start

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


class TestSequenceResetsOnNewConversation(unittest.TestCase):
    """Sequence counter (NNNN) must reset to 0001 when a new conversation starts."""

    def _make(self, idle_timeout=300):
        clock = MockClock()
        tracker = ConversationTracker(
            idle_timeout_seconds=idle_timeout,
            start_conversation_num=1,
            clock=clock,
        )
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        gen = RequestIdGenerator(
            logs_root=Path(tmp.name),
            tracker=tracker,
        )
        return gen, clock

    def test_first_request_is_0001(self):
        gen, _ = self._make()
        assert gen.next_id() == "rq-c01-0001"

    def test_sequence_increments_within_conversation(self):
        gen, clock = self._make()
        assert gen.next_id() == "rq-c01-0001"
        clock.advance(10)
        assert gen.next_id() == "rq-c01-0002"
        clock.advance(10)
        assert gen.next_id() == "rq-c01-0003"

    def test_sequence_resets_on_timeout_new_conversation(self):
        """When idle timeout triggers a new conversation, sequence resets to 0001."""
        gen, clock = self._make(idle_timeout=300)
        # Three requests in c01
        gen.next_id()  # rq-c01-0001
        clock.advance(10)
        gen.next_id()  # rq-c01-0002
        clock.advance(10)
        gen.next_id()  # rq-c01-0003

        # Idle timeout triggers c02
        clock.advance(301)
        assert gen.next_id() == "rq-c02-0001"

    def test_multiple_conversation_transitions(self):
        """Sequence resets on each conversation boundary."""
        gen, clock = self._make(idle_timeout=10)

        # c01: 2 requests
        assert gen.next_id() == "rq-c01-0001"
        clock.advance(5)
        assert gen.next_id() == "rq-c01-0002"

        # c02: 1 request
        clock.advance(11)
        assert gen.next_id() == "rq-c02-0001"

        # c03: 3 requests
        clock.advance(11)
        assert gen.next_id() == "rq-c03-0001"
        clock.advance(5)
        assert gen.next_id() == "rq-c03-0002"
        clock.advance(5)
        assert gen.next_id() == "rq-c03-0003"

        # c04: must start at 0001, not 0004
        clock.advance(11)
        assert gen.next_id() == "rq-c04-0001"

    def test_sequence_resets_when_tracker_mutated_externally(self):
        """When the diagnostic agent bumps the conversation via the tracker
        before next_id() is called, the sequence must still reset."""
        gen, clock = self._make()
        assert gen.next_id() == "rq-c01-0001"
        clock.advance(10)
        assert gen.next_id() == "rq-c01-0002"

        # Simulate diagnostic agent calling tracker.new_conversation() directly
        gen.tracker.new_conversation()
        assert gen.next_id() == "rq-c02-0001"

    def test_sequence_continues_when_reassigned_to_earlier_conversation(self):
        """When the diagnostic agent reassigns back to an earlier conversation,
        the sequence must continue from where that conversation left off,
        not restart from 0001 (which would overwrite existing logs)."""
        gen, clock = self._make()
        assert gen.next_id() == "rq-c01-0001"
        clock.advance(10)
        assert gen.next_id() == "rq-c01-0002"

        # Simulate: diagnostic agent decides this is a new topic
        gen.tracker.new_conversation()
        clock.advance(1)
        # Then another request in c02
        assert gen.next_id() == "rq-c02-0001"
        clock.advance(10)
        assert gen.next_id() == "rq-c02-0002"

        # Diagnostic agent reassigns back to c01 — must continue from 0002
        gen.tracker.reassign_current("c01", "original topic")
        assert gen.next_id() == "rq-c01-0003"

    def test_sequence_continues_after_multiple_reassignments(self):
        """Switching back and forth between conversations preserves each one's sequence."""
        gen, clock = self._make()
        # c01: 3 requests
        assert gen.next_id() == "rq-c01-0001"
        clock.advance(10)
        assert gen.next_id() == "rq-c01-0002"
        clock.advance(10)
        assert gen.next_id() == "rq-c01-0003"

        # c02: 1 request
        gen.tracker.new_conversation()
        clock.advance(1)
        assert gen.next_id() == "rq-c02-0001"

        # Back to c01: should continue at 0004
        gen.tracker.reassign_current("c01", "topic a")
        assert gen.next_id() == "rq-c01-0004"

        # Back to c02: should continue at 0002
        gen.tracker.reassign_current("c02", "topic b")
        assert gen.next_id() == "rq-c02-0002"

        # Back to c01 again: should be 0005
        gen.tracker.reassign_current("c01", "topic a")
        assert gen.next_id() == "rq-c01-0005"

    def test_reassign_to_conversation_with_no_prior_requests(self):
        """Reassigning to a conversation that was minted but never had next_id
        called should start at 0001."""
        gen, clock = self._make()
        assert gen.next_id() == "rq-c01-0001"
        clock.advance(10)

        # Mint c02 and c03 without generating IDs
        gen.tracker.new_conversation()  # c02
        gen.tracker.new_conversation()  # c03

        # Reassign to c02 — no prior requests, should start at 0001
        gen.tracker.reassign_current("c02", "new topic")
        assert gen.next_id() == "rq-c02-0001"


class TestResumeCountersFromDisk(unittest.TestCase):
    """Sequences must resume from disk after restart/reconnect."""

    def test_new_conversation_continues_sequence_from_disk(self):
        """When a new generator mints a conversation number that already has
        request directories on disk, its sequence must continue from the
        existing max rather than restarting at 0001."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        logs_root = Path(tmp.name)

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = logs_root / today

        # Prior session left c03 with 2 requests
        (today_dir / "c03" / "rq-c03-0001").mkdir(parents=True)
        (today_dir / "c03" / "rq-c03-0002").mkdir(parents=True)

        # New generator — starts at c04 (max_conversation + 1)
        gen = RequestIdGenerator(logs_root=logs_root)
        assert gen.next_id() == "rq-c04-0001"

    def test_resumed_sequences_populated_from_disk(self):
        """_resume_counters must return per-conversation sequences so that
        revisiting a conversation after restart doesn't overwrite logs."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        logs_root = Path(tmp.name)

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = logs_root / today

        # Prior session: c01 had 3 requests, c02 had 1 request
        (today_dir / "c01" / "rq-c01-0001").mkdir(parents=True)
        (today_dir / "c01" / "rq-c01-0002").mkdir(parents=True)
        (today_dir / "c01" / "rq-c01-0003").mkdir(parents=True)
        (today_dir / "c02" / "rq-c02-0001").mkdir(parents=True)

        _, conv_sequences = RequestIdGenerator._resume_counters(logs_root)
        assert conv_sequences == {"c01": 3, "c02": 1}
