"""Concurrency contract for the result store.

**A. Handle consistency** — at every observable state, every handle in
``search_history`` exists as a key in ``result_store``. No orphans.

**B. No lost writes** — N concurrent ``store_result_entries`` calls
with distinct entries all return handles, and every returned handle
either resolves in ``result_store`` or was evicted as part of the
MAX-entries cap (and evicted means absent from both search_history
and result_store).

**C. Reader-safe** — a reader accessing ``search_history`` and
``result_store`` through the class's lock never observes a handle
that's present in history but missing from the store, even under
concurrent eviction. (Readers that bypass the lock are adversarial
and not covered by the contract.)

The implementation achieves these by serialising the append+evict
pair in a single critical section and trimming history before
removing from the store.
"""

import sys
import threading
import unittest

from app.runtime.result_store_types import ResultStoreEntry
from app.runtime.state import RuntimeState
from app.settings import get_settings


def _bare_runtime_state() -> RuntimeState:
    """Minimal RuntimeState for result-store tests — no tools registered."""
    from app.runtime.result_store_manager import ResultStoreManager
    rs = object.__new__(RuntimeState)
    rs.results = ResultStoreManager()
    rs.result_store = rs.results.entries
    rs.search_history = rs.results.history
    rs.result_store_lock = rs.results.lock
    return rs


def _entry(i: int) -> ResultStoreEntry:
    """Build a unique ResultStoreEntry per thread index."""
    return ResultStoreEntry(
        items=[{"title": f"track_{i}"}],
        description=f'"query_{i}"',
        item_count=1,
        tool_name="roon_search",
        session_key=f"sess_{i}",
    )


class TestHandleConsistencyFinalState(unittest.TestCase):
    """Invariant A + B: after N concurrent stores, final state is coherent."""

    def test_concurrent_stores_preserve_handle_consistency(self):
        rs = _bare_runtime_state()
        N = 20  # more than get_settings().search_history_max_entries (5) so eviction fires

        barrier = threading.Barrier(N)
        returned_handles: list[str | None] = [None] * N
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                handles = rs.store_result_entries([_entry(i)])
                self.assertEqual(len(handles), 1)
                returned_handles[i] = handles[0]
            except BaseException as exc:  # noqa: BLE001 -- worker-thread exception capture; surfaced to main thread via the errors list after join
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertFalse(errors, f"Concurrent store raised: {errors!r}")
        self.assertTrue(
            all(h is not None for h in returned_handles),
            "Not every thread returned a handle",
        )

        history_handles = {e.result_handle for e in rs.search_history}
        store_keys = set(rs.result_store.keys())

        # Invariant A: every handle in search_history exists in result_store
        orphans = history_handles - store_keys
        self.assertFalse(
            orphans,
            f"Handles in search_history but missing from result_store: {orphans}",
        )

        # Invariant B (no lost writes): every handle in result_store is also
        # in search_history. Handles that fell off the end were supposed to
        # go through _remove_from_result_store; leaked handles (in store but
        # not in history) indicate a non-atomic append+evict race dropped a
        # history entry without evicting its store payload.
        leaks = store_keys - history_handles
        self.assertFalse(
            leaks,
            f"Handles in result_store but missing from search_history: {leaks}",
        )

        # Invariant B bound: search_history stays within the cap
        self.assertLessEqual(
            len(rs.search_history),
            get_settings().search_history_max_entries,
            "search_history exceeded the MAX_ENTRIES cap",
        )

        # With N > MAX, we expect exactly MAX survivors on both sides
        self.assertEqual(len(rs.search_history), get_settings().search_history_max_entries)
        self.assertEqual(len(rs.result_store), get_settings().search_history_max_entries)


class TestReaderSafeDuringEviction(unittest.TestCase):
    """Invariant C: a reader iterating search_history and dereferencing
    against result_store must never see an orphaned handle, even while
    a writer is concurrently evicting old entries."""

    def setUp(self) -> None:
        self._original_interval = sys.getswitchinterval()
        # Maximise GIL-switch opportunities so the eviction race is
        # more likely to be observed within the iteration budget.
        sys.setswitchinterval(1e-6)

    def tearDown(self) -> None:
        sys.setswitchinterval(self._original_interval)

    def test_reader_never_sees_orphan_during_eviction(self):
        rs = _bare_runtime_state()

        barrier = threading.Barrier(2)
        stop = threading.Event()
        violations: list[str] = []
        errors: list[BaseException] = []

        WRITER_ITERATIONS = 400

        def writer() -> None:
            barrier.wait()
            try:
                for i in range(WRITER_ITERATIONS):
                    rs.store_result_entries([_entry(i)])
            except BaseException as exc:  # noqa: BLE001 -- worker-thread exception capture; surfaced to main thread via the errors list after join
                errors.append(exc)
            finally:
                stop.set()

        def reader() -> None:
            barrier.wait()
            try:
                while not stop.is_set():
                    # Access search_history + result_store through the
                    # class's lock so the snapshot and dereferences are
                    # a single atomic view. Production readers either use
                    # decorated accessors (_render_search_history,
                    # _find_history_entry_by_session) or take the lock
                    # explicitly — any reader that bypasses both is by
                    # contract not covered.
                    with rs.result_store_lock:
                        for entry in rs.search_history:
                            if entry.result_handle not in rs.result_store:
                                violations.append(
                                    f"Handle {entry.result_handle} in search_history "
                                    f"but missing from result_store",
                                )
                                return
            except BaseException as exc:  # noqa: BLE001 -- worker-thread exception capture; surfaced to main thread via the errors list after join
                errors.append(exc)

        t_w = threading.Thread(target=writer, name="writer")
        t_r = threading.Thread(target=reader, name="reader")
        t_w.start()
        t_r.start()
        t_w.join(timeout=30)
        t_r.join(timeout=30)

        self.assertFalse(
            t_w.is_alive() or t_r.is_alive(),
            "Threads did not complete — possible deadlock",
        )
        self.assertFalse(errors, f"Thread raised: {errors!r}")
        self.assertFalse(
            violations,
            f"Reader observed orphaned handle: {violations[0] if violations else ''}",
        )


class TestStoreResultHandleConcurrency(unittest.TestCase):
    """store_result_handle also involves counter + handle + store mutations.
    Concurrent calls must produce distinct handles and store them all."""

    def test_concurrent_store_result_handle_produces_distinct_handles(self):
        rs = _bare_runtime_state()
        N = 50
        barrier = threading.Barrier(N)
        handles: list[str | None] = [None] * N
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                handles[i] = rs.store_result_handle({"i": i})
            except BaseException as exc:  # noqa: BLE001 -- worker-thread exception capture; surfaced to main thread via the errors list after join
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertFalse(errors, f"store_result_handle raised: {errors!r}")

        # Every handle should be distinct (counter is monotonic)
        distinct = {h for h in handles if h is not None}
        self.assertEqual(
            len(distinct), N,
            f"Expected {N} distinct handles, got {len(distinct)}",
        )

        # Every handle should resolve in the store (no eviction in this path)
        for h in handles:
            self.assertIn(h, rs.result_store)


if __name__ == "__main__":
    unittest.main()
