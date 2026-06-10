"""Contract: ``ensure_initialised()`` is all-or-nothing.

1. **All-or-nothing success** — after a return, ``self.initialised``
   is True and every post-condition holds (clients set, tools
   registered, roon_connection wired, event listener bound exactly
   once).
2. **Clean retry on failure** — after a raise, ``self.initialised``
   is False and a subsequent call with the failure condition removed
   produces the same result as a clean first-time init. No duplicate
   tool registrations, no leaked event listeners, no orphaned
   RoonConnection subscriptions, no doubled-up log-dir side effects.
3. **Idempotent on success** — a second call after a successful init
   does nothing.

The implementation uses a clean-on-retry approach: on entry, if
``self.initialised`` is False but partial state is detected, reset
before running init fresh.
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.runtime.state import RuntimeState  # noqa: E402

# ------------------------------------------------------------------ #
#  Fakes that track side effects                                      #
# ------------------------------------------------------------------ #

class _FakeApi:
    def __init__(self) -> None:
        self.zones: dict = {}


class _TrackingRoonConnection:
    """Fake RoonConnection that records each instance and its listeners.

    ``instances`` is a per-class list (reset() creates a subclass-specific
    attribute; __init__ appends via ``type(self).instances`` so subclass
    instances don't pollute the parent's list).
    """

    instances: list["_TrackingRoonConnection"] = []

    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)
        self.api = _FakeApi()
        self.listeners: list = []
        type(self).instances.append(self)

    def register_event_listener(self, listener) -> None:
        self.listeners.append(listener)

    def get_default_zone(self):
        return None

    @classmethod
    def reset(cls) -> None:
        cls.instances = []


@contextmanager
def _patched_init(
    extra_env: dict | None = None,
    skill_docs: list | None = None,
    roon_connection_cls: type | None = None,
):
    """Patch external deps for ensure_initialised() and yield the runtime."""
    runtime = RuntimeState()
    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
    }
    if extra_env:
        env.update(extra_env)

    roon_cls = roon_connection_cls or _TrackingRoonConnection

    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", roon_cls),
        patch("app.runtime.state._load_agent_skills", return_value=skill_docs or []),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("<available_skills />", ""),
        ),
    ):
        yield runtime


# ------------------------------------------------------------------ #
#  Tests: all-or-nothing success contract                             #
# ------------------------------------------------------------------ #

class TestAllOrNothingSuccess(unittest.TestCase):
    def setUp(self) -> None:
        _TrackingRoonConnection.reset()

    def test_successful_init_sets_all_post_conditions(self):
        with _patched_init() as runtime:
            runtime.ensure_initialised()

        self.assertTrue(runtime.initialised, "initialised flag not set")
        self.assertIsNotNone(runtime.llm_client, "llm_client not wired")
        self.assertIsNotNone(runtime.arbiter_client, "arbiter_client not wired")
        self.assertIsNotNone(runtime.diagnostic_client, "diagnostic_client not wired")
        self.assertIsNotNone(runtime.roon_connection, "roon_connection not wired")
        self.assertGreater(
            len(runtime.tool_registry), 0,
            "no tools registered",
        )

        # Exactly one RoonConnection was created and its listener registered once
        self.assertEqual(
            len(_TrackingRoonConnection.instances), 1,
            f"expected 1 RoonConnection, got {len(_TrackingRoonConnection.instances)}",
        )
        self.assertEqual(
            len(_TrackingRoonConnection.instances[0].listeners), 2,
            "event listeners should be registered exactly once "
            "(forward + play-history = 2)",
        )


class TestIdempotenceOnSuccess(unittest.TestCase):
    def setUp(self) -> None:
        _TrackingRoonConnection.reset()

    def test_second_call_after_success_is_noop(self):
        with _patched_init() as runtime:
            runtime.ensure_initialised()
            first_llm_client = runtime.llm_client
            first_tool_count = len(runtime.tool_registry)

            runtime.ensure_initialised()  # second call

            # Identity preserved (no re-creation), not just equivalence
            self.assertIs(runtime.llm_client, first_llm_client)
            self.assertEqual(len(runtime.tool_registry), first_tool_count)

        # Still exactly one RoonConnection, still the same listener set
        # (forward + play-history = 2)
        self.assertEqual(len(_TrackingRoonConnection.instances), 1)
        self.assertEqual(len(_TrackingRoonConnection.instances[0].listeners), 2)


# ------------------------------------------------------------------ #
#  Tests: failed init leaves no observable state for retry            #
# ------------------------------------------------------------------ #

class _FirstCallFailsRoonConnection(_TrackingRoonConnection):
    """RoonConnection that raises on the first instantiation and
    succeeds on the second. Simulates a transient pairing failure."""

    _call_count = 0

    def __init__(self, *args, **kwargs) -> None:
        type(self)._call_count += 1
        if type(self)._call_count == 1:
            raise ConnectionError("transient pairing failure")
        super().__init__(*args, **kwargs)

    @classmethod
    def reset(cls) -> None:
        super().reset()
        cls._call_count = 0


class TestRetryAfterEarlyFailure(unittest.TestCase):
    """Failure at RoonConnection creation — before any event listener
    is registered, before tools are instantiated."""

    def setUp(self) -> None:
        _FirstCallFailsRoonConnection.reset()

    def test_retry_after_connection_failure_is_clean(self):
        with _patched_init(roon_connection_cls=_FirstCallFailsRoonConnection) as runtime:
            with self.assertRaises(ConnectionError):
                runtime.ensure_initialised()

            # Contract: after raise, initialised is False
            self.assertFalse(runtime.initialised, "initialised set after raise")

            # Retry should succeed and produce clean state
            runtime.ensure_initialised()

        # Only the second (successful) RoonConnection should be live; the first
        # raised in __init__ and never got stored. Contract post-retry:
        # - exactly one reachable RoonConnection via runtime.roon_connection
        # - that connection has exactly one event listener
        self.assertTrue(runtime.initialised)
        self.assertIsNotNone(runtime.roon_connection)
        self.assertEqual(
            len(runtime.roon_connection.listeners), 2,
            "event listeners should be registered exactly once on the "
            "live connection (forward + play-history = 2)",
        )


class _LateFailureRoonConnection(_TrackingRoonConnection):
    """RoonConnection whose register_event_listener raises on first call
    and succeeds on second. Simulates a failure AFTER RoonConnection
    creation but before tool registration."""

    _listener_attempts = 0

    def register_event_listener(self, listener) -> None:
        type(self)._listener_attempts += 1
        if type(self)._listener_attempts == 1:
            raise RuntimeError("listener registration failed")
        super().register_event_listener(listener)

    @classmethod
    def reset(cls) -> None:
        super().reset()
        cls._listener_attempts = 0


class TestRetryAfterLateFailure(unittest.TestCase):
    """Failure at event listener registration — after RoonConnection is
    created and stored on self.roon_connection. This is the scenario
    where the current code leaks: on retry a second RoonConnection is
    created and the original one stays GC-reachable via closures or
    the library's internal registry."""

    def setUp(self) -> None:
        _LateFailureRoonConnection.reset()

    def test_retry_after_late_failure_yields_clean_runtime(self):
        """Contract: after retry, runtime.roon_connection is the live one
        with exactly one listener, and the event listener fires exactly
        once per Roon event (no doubling).

        The strict "exactly one instance was ever created" goal would
        require proper teardown of the failed instance (a roonapi
        disconnect API we don't currently wrap); clean-on-retry here
        means runtime holds only the live connection, and listeners are
        registered only on that connection. Leaked failed instances may
        linger in memory briefly before GC — tolerable."""
        with _patched_init(roon_connection_cls=_LateFailureRoonConnection) as runtime:
            with self.assertRaises(RuntimeError):
                runtime.ensure_initialised()

            self.assertFalse(runtime.initialised)

            runtime.ensure_initialised()  # retry

        # Runtime's live connection has the expected listener set, no doubling
        # (forward + play-history = 2)
        self.assertTrue(runtime.initialised)
        self.assertIsNotNone(runtime.roon_connection)
        self.assertEqual(len(runtime.roon_connection.listeners), 2)

        # Only the live connection has listeners. The failed first instance
        # has listeners=[] (register_event_listener raised before it could
        # record). No previously-created instance should have a non-empty
        # listener list that would cause double-event-firing.
        connections_with_listeners = [
            c for c in _LateFailureRoonConnection.instances if c.listeners
        ]
        self.assertEqual(
            len(connections_with_listeners), 1,
            "More than one RoonConnection has listeners — Roon events will double-fire",
        )
        self.assertIs(connections_with_listeners[0], runtime.roon_connection)


class TestRetryAfterToolRegistrationFailure(unittest.TestCase):
    """Failure during tool registration — after tools are partially
    registered in tool_registry. On retry with clean-on-retry, the
    registry must end up with exactly the expected tools (no warnings
    about duplicate registration)."""

    def setUp(self) -> None:
        _TrackingRoonConnection.reset()

    def test_retry_after_partial_tool_registration_is_clean(self):
        with _patched_init() as runtime:
            # Inject failure on the 3rd tool registration, then let it
            # succeed on retry.
            original_register = runtime.tool_registry.register
            call_state = {"count": 0, "fail_on_attempt": 1}

            def flaky_register(*args, **kwargs):
                call_state["count"] += 1
                if call_state["count"] == 3 and call_state["fail_on_attempt"] == 1:
                    raise RuntimeError("flaky tool registration")
                original_register(*args, **kwargs)

            with patch.object(runtime.tool_registry, "register", flaky_register):
                with self.assertRaises(RuntimeError):
                    runtime.ensure_initialised()

            self.assertFalse(runtime.initialised)

            # Flip so retry succeeds — reset the mock counter so
            # ensure_initialised's registration phase runs normally.
            call_state["fail_on_attempt"] = 999
            call_state["count"] = 0

            import logging
            with self.assertNoLogs("swarpius.tool_registry", level=logging.WARNING):
                runtime.ensure_initialised()

        # Contract: after retry the registry has the expected tool count
        # (no duplicates from the partial first attempt).
        self.assertTrue(runtime.initialised)
        tool_names = runtime.tool_registry.tool_names
        self.assertEqual(
            len(tool_names), len(set(tool_names)),
            "duplicate tool names in registry",
        )


if __name__ == "__main__":
    unittest.main()
