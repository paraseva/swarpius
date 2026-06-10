"""Two-tap Ctrl+C cancellation for CLI mode.

The state machine is split out into :class:`CancelHandler` so it can
be exercised directly without orchestrating real signal delivery
(``_thread.interrupt_main`` interacts unreliably with pytest's own
SIGINT handling).

End-to-end Ctrl+C behaviour — the run_cli_loop wiring and the
threading wait loop — is verified manually; the threading wrapper
itself is covered here for the no-interrupt paths only.
"""

from __future__ import annotations

import threading
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.cli.runner import CancelHandler, run_request_with_cancel  # noqa: E402


class TestCancelHandler(unittest.TestCase):
    def test_first_interrupt_sets_event_and_returns_false(self) -> None:
        cancel = threading.Event()
        first_calls: list[None] = []
        handler = CancelHandler(cancel, on_first=lambda: first_calls.append(None))
        result = handler.handle_interrupt()
        self.assertFalse(result)
        self.assertTrue(cancel.is_set())
        self.assertEqual(len(first_calls), 1)

    def test_second_interrupt_returns_true_and_calls_on_second(self) -> None:
        cancel = threading.Event()
        second_calls: list[None] = []
        handler = CancelHandler(cancel, on_second=lambda: second_calls.append(None))
        handler.handle_interrupt()
        result = handler.handle_interrupt()
        self.assertTrue(result)
        self.assertEqual(len(second_calls), 1)

    def test_third_interrupt_keeps_returning_true(self) -> None:
        """A worker that ignores cancel_event shouldn't be able to
        trap the user — every interrupt after the second still
        signals exit."""
        cancel = threading.Event()
        on_second_count = [0]

        def on_second() -> None:
            on_second_count[0] += 1

        handler = CancelHandler(cancel, on_second=on_second)
        handler.handle_interrupt()  # 1st: on_first, return False
        self.assertTrue(handler.handle_interrupt())  # 2nd: on_second
        self.assertTrue(handler.handle_interrupt())  # 3rd: on_second again
        # ``on_second`` fires on every interrupt past the first —
        # not deduplicated, so a user repeatedly mashing Ctrl+C on
        # a stuck worker keeps getting acknowledged.
        self.assertEqual(on_second_count[0], 2)

    def test_callbacks_are_optional(self) -> None:
        cancel = threading.Event()
        handler = CancelHandler(cancel)
        self.assertFalse(handler.handle_interrupt())
        self.assertTrue(cancel.is_set())
        self.assertTrue(handler.handle_interrupt())


class TestRunRequestNoInterrupt(unittest.TestCase):
    def test_returns_false_when_target_completes(self) -> None:
        ran = threading.Event()

        def target(cancel_event: threading.Event) -> None:
            ran.set()

        exit_requested, exc = run_request_with_cancel(target=target)
        self.assertFalse(exit_requested)
        self.assertIsNone(exc)
        self.assertTrue(ran.is_set())

    def test_target_receives_unset_cancel_event(self) -> None:
        seen: list[threading.Event] = []

        def target(cancel_event: threading.Event) -> None:
            seen.append(cancel_event)

        run_request_with_cancel(target=target)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], threading.Event)
        self.assertFalse(seen[0].is_set())

    def test_exception_in_target_is_captured_not_propagated(self) -> None:
        def target(cancel_event: threading.Event) -> None:
            raise RuntimeError("boom")

        exit_requested, exc = run_request_with_cancel(target=target)
        self.assertFalse(exit_requested)
        self.assertIsInstance(exc, RuntimeError)
        self.assertEqual(str(exc), "boom")


if __name__ == "__main__":
    unittest.main()
