"""Concurrency tests for the zone-state lock.

Background: ``_forward_roon_live_event`` runs on the roonapi library's
dispatch thread and mutates ``zone_aliases`` / ``group_names`` /
``_zone_group_ids`` / ``_zone_cache`` on RuntimeState. The main request
thread reads those structures. A Roon event fired during a reader's
iteration would raise RuntimeError("dictionary changed size during
iteration") without the ``zone_state_lock`` / ``@_locks_zone_state``
decorator.

These tests defend the fix in two ways:

1. Static: every method flagged by the audit as a zone-state accessor
   is confirmed to have @_locks_zone_state applied (via the attribute
   functools.wraps sets). This catches a future edit that removes the
   decorator from any protected method, deterministically.

2. Dynamic: a concurrency stress that would raise RuntimeError if the
   lock were absent from the chosen reader or writer. Less
   deterministic under GIL timing, but confirms behaviour end-to-end.
"""

import sys
import threading
import unittest
from unittest.mock import MagicMock

from app.roon.zone_domain import ZoneDomain
from app.runtime.state import RuntimeState, _locks_zone_state

# Methods whose bodies mutate or iterate the zone-state dicts; each
# must have @_locks_zone_state applied. The static test below checks
# each method in its current home (some on ZoneDomain, some still on
# RuntimeState).
#
# (method_name, owner_class) — owner is the class the decorator
# applies to.
ZONE_STATE_PROTECTED_METHODS = (
    ("get_zone_aliases_context", ZoneDomain),
    ("get_zone_status_context", ZoneDomain),
    ("format_zone_label", ZoneDomain),
    ("reconcile_zone_state", ZoneDomain),
    ("resolve_zone_name", ZoneDomain),
    ("resolve_zone_name_fuzzy", ZoneDomain),
    ("get_alias_for_zone", ZoneDomain),
    ("perform_config_action", RuntimeState),
)


class TestDecoratorContract(unittest.TestCase):
    """The @_locks_zone_state decorator acquires self.zone_state_lock
    around the wrapped method body."""

    def test_decorator_enters_and_exits_instance_lock(self):
        class Fake:
            def __init__(self) -> None:
                self.zone_state_lock = MagicMock()

            @_locks_zone_state
            def do_it(self, value: int) -> int:
                return value * 2

        fake = Fake()
        result = fake.do_it(21)
        self.assertEqual(result, 42)
        fake.zone_state_lock.__enter__.assert_called_once()
        fake.zone_state_lock.__exit__.assert_called_once()

    def test_decorator_releases_on_exception(self):
        class Fake:
            def __init__(self) -> None:
                self.zone_state_lock = MagicMock()

            @_locks_zone_state
            def boom(self) -> None:
                raise ValueError("explode")

        fake = Fake()
        with self.assertRaises(ValueError):
            fake.boom()
        fake.zone_state_lock.__enter__.assert_called_once()
        fake.zone_state_lock.__exit__.assert_called_once()

    def test_rlock_allows_nested_decorated_calls(self):
        """RuntimeState uses RLock so methods decorated with
        @_locks_zone_state can call other decorated methods on the
        same instance without self-deadlocking."""
        class Fake:
            def __init__(self) -> None:
                self.zone_state_lock = threading.RLock()

            @_locks_zone_state
            def outer(self) -> int:
                return self.inner() + 1

            @_locks_zone_state
            def inner(self) -> int:
                return 41

        self.assertEqual(Fake().outer(), 42)


class TestProtectedMethodsStatic(unittest.TestCase):
    """Static check: every audit-flagged zone-state method has the
    decorator applied. Deterministic; fails as soon as the decorator
    is removed from any protected method in a later edit."""

    def test_every_protected_method_carries_the_decorator(self):
        missing: list[str] = []
        for name, owner in ZONE_STATE_PROTECTED_METHODS:
            method = getattr(owner, name, None)
            self.assertIsNotNone(method, f"{owner.__name__}.{name} no longer exists")
            # @_locks_zone_state uses functools.wraps, so the wrapper has
            # __wrapped__ pointing at the original undecorated method.
            if not hasattr(method, "__wrapped__"):
                missing.append(f"{owner.__name__}.{name}")
        self.assertFalse(
            missing,
            f"Missing @_locks_zone_state on: {missing}. "
            "Concurrent Roon events will race with request-thread reads.",
        )


_td_keepalive: list = []


def _make_runtime_with_aliases(aliases: dict) -> RuntimeState:
    """Build a minimal RuntimeState sufficient for zone-alias operations."""
    from tests._runtime_fixtures import wire_zone_domain
    rs = object.__new__(RuntimeState)
    rs.roon_connection = None
    rs._ws_send_callback = lambda _c, _p: None
    td = wire_zone_domain(rs)
    if td is not None:
        _td_keepalive.append(td)
    rs.zone_aliases.update(aliases)
    return rs


class TestZoneAliasesConcurrency(unittest.TestCase):
    """End-to-end stress: concurrent mutation and iteration through
    @_locks_zone_state-protected methods never raises."""

    def setUp(self) -> None:
        # Maximise GIL-switch opportunities so a missing lock is more
        # likely to be observed within the iteration budget.
        self._original_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)

    def tearDown(self) -> None:
        sys.setswitchinterval(self._original_interval)

    def test_concurrent_mutation_and_read_is_safe(self):
        # Large dict × many reader iterations keeps the reader in the
        # loop long enough for the writer to mutate mid-iteration.
        initial = {f"alias_{i}": "ZoneA" for i in range(2000)}
        rs = _make_runtime_with_aliases(initial)

        # Writer: decorated method that deletes and re-adds a rotating
        # block of keys. @_locks_zone_state ensures it serialises with
        # any decorated reader on the same instance.
        @_locks_zone_state
        def flip(self) -> None:
            for i in range(200):
                key = f"flip_{i}"
                if key in self.zone_aliases:
                    del self.zone_aliases[key]
                else:
                    self.zone_aliases[key] = "ZoneA"

        rs.flip = flip.__get__(rs, type(rs))

        barrier = threading.Barrier(2)
        stop = threading.Event()
        errors: list[BaseException] = []

        WRITER_ITERATIONS = 300

        def writer() -> None:
            barrier.wait()
            try:
                for _ in range(WRITER_ITERATIONS):
                    rs.flip()
            except BaseException as exc:  # noqa: BLE001 -- worker-thread exception capture; surfaced to main thread via the errors list after join
                errors.append(exc)
            finally:
                stop.set()

        def reader() -> None:
            barrier.wait()
            try:
                while not stop.is_set():
                    # _get_alias_for_zone iterates zone_aliases under
                    # the zone_state_lock.
                    rs._get_alias_for_zone("ZoneA")
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
            "Threads failed to complete — possible deadlock",
        )
        self.assertFalse(
            errors,
            f"Concurrent mutation + iteration raised: {errors!r}",
        )


if __name__ == "__main__":
    unittest.main()
