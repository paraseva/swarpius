"""Tests for ConversationTracker: timeout-based and smart conversation assignment."""

import unittest

from app.runtime.conversation_tracker import ConversationTracker


class MockClock:
    """Deterministic clock for testing — no sleeps needed."""

    def __init__(self, start: float = 1000.0):
        self._time = start

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


class TestTimeoutAssignment(unittest.TestCase):
    """Timeout-based conversation assignment."""

    def _make(self, idle_timeout=300, start_num=1, **kwargs):
        clock = MockClock()
        tracker = ConversationTracker(
            idle_timeout_seconds=idle_timeout,
            start_conversation_num=start_num,
            clock=clock,
            **kwargs,
        )
        return tracker, clock

    def test_first_request_creates_conversation(self):
        tracker, _ = self._make()
        assert tracker.assign_by_timeout() == "c01"
        assert tracker.current_id == "c01"

    def test_subsequent_request_continues(self):
        tracker, clock = self._make()
        tracker.assign_by_timeout()
        clock.advance(60)
        assert tracker.assign_by_timeout() == "c01"

    def test_at_timeout_boundary_starts_new(self):
        tracker, clock = self._make(idle_timeout=300)
        tracker.assign_by_timeout()
        clock.advance(300)
        assert tracker.assign_by_timeout() == "c02"

    def test_multiple_timeouts_increment(self):
        tracker, clock = self._make(idle_timeout=10)
        for expected_num in range(1, 5):
            assert tracker.assign_by_timeout() == f"c{expected_num:02d}"
            clock.advance(11)

    def test_explicit_new_conversation(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()
        assert tracker.new_conversation() == "c02"
        assert tracker.current_id == "c02"

    def test_start_conversation_num(self):
        tracker, _ = self._make(start_num=5)
        assert tracker.assign_by_timeout() == "c05"

    def test_request_count_increments(self):
        tracker, clock = self._make()
        tracker.assign_by_timeout()
        clock.advance(10)
        tracker.assign_by_timeout()
        clock.advance(10)
        tracker.assign_by_timeout()
        threads = tracker.get_active_threads()
        assert threads[0].request_count == 3

    def test_current_id_before_any_assignment(self):
        """current_id returns a sensible default before first assignment."""
        tracker, _ = self._make(start_num=3)
        assert tracker.current_id == "c03"


class TestActiveThreads(unittest.TestCase):
    """get_active_threads() listing and aging."""

    def _make(self, **kwargs):
        clock = MockClock()
        tracker = ConversationTracker(clock=clock, **kwargs)
        return tracker, clock

    def test_empty_before_any_assignment(self):
        tracker, _ = self._make()
        assert tracker.get_active_threads() == []

    def test_single_thread(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()
        threads = tracker.get_active_threads()
        assert len(threads) == 1
        assert threads[0].id == "c01"

    def test_multiple_threads_sorted_most_recent_first(self):
        tracker, clock = self._make(idle_timeout_seconds=10)
        tracker.assign_by_timeout()  # c01
        clock.advance(11)
        tracker.assign_by_timeout()  # c02
        threads = tracker.get_active_threads()
        assert [t.id for t in threads] == ["c02", "c01"]

    def test_aged_threads_excluded(self):
        tracker, clock = self._make(idle_timeout_seconds=10, aging_hours=1.0)
        tracker.assign_by_timeout()  # c01 at t=1000
        clock.advance(11)
        tracker.assign_by_timeout()  # c02 at t=1011
        clock.advance(3590)  # now t=4601; c01 age=3601>3600, c02 age=3590<3600
        threads = tracker.get_active_threads()
        assert len(threads) == 1
        assert threads[0].id == "c02"

    def test_max_conversations_caps_results(self):
        tracker, clock = self._make(idle_timeout_seconds=1, max_conversations=3)
        for _ in range(5):
            tracker.assign_by_timeout()
            clock.advance(2)
        threads = tracker.get_active_threads()
        assert len(threads) <= 3
        # Should be the 3 most recent
        assert threads[0].id == "c05"


class TestTopicUpdates(unittest.TestCase):
    """Topic summary management for diagnostic agent integration."""

    def _make(self, **kwargs):
        clock = MockClock()
        tracker = ConversationTracker(clock=clock, **kwargs)
        return tracker, clock

    def test_update_topic(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()
        tracker.update_topic("c01", "Playing jazz music")
        assert tracker.get_active_threads()[0].topic_summary == "Playing jazz music"

    def test_update_topic_unknown_id_is_noop(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()
        tracker.update_topic("c99", "Nonexistent")  # should not raise

    def test_default_topic_is_empty(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()
        assert tracker.get_active_threads()[0].topic_summary == ""

    def test_reassign_current_to_existing(self):
        tracker, clock = self._make(idle_timeout_seconds=10)
        tracker.assign_by_timeout()  # c01
        tracker.update_topic("c01", "First topic")
        clock.advance(11)
        tracker.assign_by_timeout()  # c02 (timeout)
        assert tracker.current_id == "c02"
        # Diagnostic agent says this belongs to c01
        tracker.reassign_current("c01", "First topic continued")
        assert tracker.current_id == "c01"
        # Topic was updated
        c01 = [t for t in tracker.get_active_threads() if t.id == "c01"][0]
        assert c01.topic_summary == "First topic continued"

    def test_reassign_survives_subsequent_timeout_check(self):
        """Diagnostic agent reassigns before assign_by_timeout runs.

        Real flow: request 1 → assign_by_timeout (c01). Idle timeout passes.
        Request 2 → diagnostic agent calls reassign_current("c01") THEN
        assign_by_timeout runs. It must honour the reassignment, not mint c02.
        """
        tracker, clock = self._make(idle_timeout_seconds=10)
        tracker.assign_by_timeout()  # c01, _last_request_time = T
        clock.advance(11)  # idle timeout exceeded
        # Diagnostic agent runs first on request 2, reassigns to c01
        tracker.reassign_current("c01", "Back to first topic")
        # assign_by_timeout must continue c01, not override with c02
        assert tracker.assign_by_timeout() == "c01"

    def test_reassign_unknown_id_is_noop(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()  # c01
        tracker.reassign_current("c99", "Nonexistent")
        assert tracker.current_id == "c01"  # unchanged


class TestLastResponseTracking(unittest.TestCase):
    """Last response summary storage for diagnostic agent context."""

    def _make(self, **kwargs):
        clock = MockClock()
        tracker = ConversationTracker(clock=clock, **kwargs)
        return tracker, clock

    def test_set_and_get_last_response(self):
        tracker, _ = self._make()
        tracker.assign_by_timeout()  # c01
        tracker.set_last_response("c01", "Now playing Take Five")
        thread = tracker.get_active_threads()[0]
        assert thread.last_response_summary == "Now playing Take Five"

    def test_set_last_response_unknown_id_is_noop(self):
        tracker, _ = self._make()
        tracker.set_last_response("c99", "Something")  # should not raise

    def test_now_property(self):
        clock = MockClock(start=5000.0)
        tracker = ConversationTracker(clock=clock)
        assert tracker.now == 5000.0
        clock.advance(100)
        assert tracker.now == 5100.0


class TestPersistence(unittest.TestCase):
    """Capture/restore so conversation grouping survives a restart."""

    def test_round_trip_continues_conversation_and_topics(self):
        clock = MockClock()
        original = ConversationTracker(clock=clock)
        original.assign_by_timeout()  # c01
        original.update_topic("c01", "jazz")
        original.set_last_response("c01", "Playing Take Five")

        restored = ConversationTracker(clock=clock)
        restored.restore_state(original.capture_state())

        clock.advance(60)  # within idle window
        self.assertEqual(restored.assign_by_timeout(), "c01")
        thread = restored.get_active_threads()[0]
        self.assertEqual(thread.topic_summary, "jazz")
        self.assertEqual(thread.last_response_summary, "Playing Take Five")

        clock.advance(10_000)  # past idle window → next conversation continues numbering
        self.assertEqual(restored.assign_by_timeout(), "c02")

    def test_default_clock_is_wall_clock(self):
        # The default must be wall-clock so a persisted last-request time is
        # comparable across processes (a monotonic value would not be).
        import time
        tracker = ConversationTracker()
        self.assertAlmostEqual(tracker.now, time.time(), delta=2.0)


class TestClearActive(unittest.TestCase):
    """clear_active(): drop active threads + the current pointer, keep the
    counter — so a conversation-history clear opens a fresh conversation
    (cNN+1) rather than continuing the cleared one or restarting numbering."""

    def _make(self, idle_timeout=300):
        clock = MockClock()
        tracker = ConversationTracker(idle_timeout_seconds=idle_timeout, clock=clock)
        return tracker, clock

    def test_drops_threads_and_advances_counter(self):
        tracker, clock = self._make(idle_timeout=10)
        tracker.assign_by_timeout()  # c01
        clock.advance(11)
        tracker.assign_by_timeout()  # c02
        tracker.update_topic("c02", "playing inna")
        tracker.clear_active()
        # Diagnostic agent sees a clean slate.
        assert tracker.get_active_threads() == []
        # Next request opens a fresh conversation: counter kept (c03).
        assert tracker.assign_by_timeout() == "c03"

    def test_within_timeout_after_clear_does_not_crash(self):
        # Clearing the threads must also null current_id, else assign_by_timeout
        # KeyErrors on the now-missing current thread within the idle window.
        tracker, clock = self._make(idle_timeout=300)
        tracker.assign_by_timeout()  # c01
        tracker.clear_active()
        clock.advance(5)  # within the idle timeout
        assert tracker.assign_by_timeout() == "c02"
