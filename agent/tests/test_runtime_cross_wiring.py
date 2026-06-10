"""Cross-wiring smoke tests: after ``ensure_initialised``, the
subsystems hold the right collaborator references.

The existing init tests in ``test_runtime_init_partial_failure``
verify that things EXIST after init (``runtime.tool_registry`` is
non-empty, ``runtime.roon_connection`` is wired). These tests verify
the next layer down: that the *same* object references flow from
RuntimeState into the tools and back, so a mutation through one path
is observable through the other.

These will be the regression net for an upcoming facade-class
extraction of RuntimeState — if a manager is built with a stale or
forgotten dependency, these assertions break.
"""

from __future__ import annotations

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


class _FakeApi:
    def __init__(self) -> None:
        self.zones: dict = {}


class _StubRoonConnection:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)
        self.api = _FakeApi()
        self.listeners: list = []

    def register_event_listener(self, listener) -> None:
        self.listeners.append(listener)

    def get_default_zone(self):
        return None


@contextmanager
def _initialised_runtime():
    runtime = RuntimeState()
    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", _StubRoonConnection),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("<available_skills />", ""),
        ),
    ):
        runtime.ensure_initialised()
        yield runtime


class TestEveryRoonToolHoldsTheSameRoonConnection(unittest.TestCase):
    """If init builds tools and the RoonConnection separately and a
    refactor accidentally passes a stale reference, this fails.
    Identity check (``is``), not equality."""

    def test_roon_tools_share_runtime_roon_connection(self):
        with _initialised_runtime() as runtime:
            for tool_name in ("roon_search", "roon_action", "roon_status", "roon_config"):
                tool = runtime.tool_registry.get(tool_name).tool_instance
                self.assertIs(
                    tool.roon_connection, runtime.roon_connection,
                    f"{tool_name} holds a different RoonConnection than the runtime",
                )


class TestResultStoreIsSharedByReference(unittest.TestCase):
    """Tools mutate the result store through their captured reference;
    if the facade passes a copy instead of the live dict, the runtime
    can't see what the tools store."""

    def test_result_fetch_tool_shares_runtime_result_store(self):
        with _initialised_runtime() as runtime:
            tool = runtime.tool_registry.get("result_fetch").tool_instance
            self.assertIs(tool.result_store, runtime.result_store)

    def test_result_fetch_tool_shares_runtime_search_history(self):
        with _initialised_runtime() as runtime:
            tool = runtime.tool_registry.get("result_fetch").tool_instance
            self.assertIs(tool.search_history, runtime.search_history)

    def test_roon_action_tool_shares_runtime_result_store(self):
        with _initialised_runtime() as runtime:
            tool = runtime.tool_registry.get("roon_action").tool_instance
            self.assertIs(tool._result_store, runtime.result_store)


class TestResolveZoneCallableIsLive(unittest.TestCase):
    """``roon_action`` captures ``runtime.resolve_zone_name`` as the
    zone resolver. A facade refactor that snapshots the callable (or
    binds it to a stale manager) would break alias resolution for any
    tool call after init. The contract is bound-method identity:
    tool sees exactly the same method object the runtime exposes."""

    def test_action_tool_captures_runtime_resolve_zone_name(self):
        with _initialised_runtime() as runtime:
            tool = runtime.tool_registry.get("roon_action").tool_instance
            # Bound-method equality compares __func__ + __self__; both
            # must match for the captured callable to be the runtime's
            # method on the runtime instance.
            self.assertEqual(
                tool._resolve_zone.__func__, runtime.resolve_zone_name.__func__,
            )
            self.assertIs(
                tool._resolve_zone.__self__, runtime.resolve_zone_name.__self__,
            )


class TestSkillsProviderReflectsRegisteredTools(unittest.TestCase):
    """The skills provider's content is set during _setup_skills_and_prompt
    *after* the tool registry is populated. The provider holds what
    _format_agent_skills_for_prompt returned — a regression where
    skill loading runs against the wrong tool set (or before tools
    are registered) would store a different string."""

    def test_skills_provider_holds_formatted_block(self):
        with _initialised_runtime() as runtime:
            # The patched _format_agent_skills_for_prompt returns
            # ("<available_skills />", "") in the fixture; the provider
            # should hold the first element.
            self.assertEqual(runtime.skills_provider.value, "<available_skills />")


class TestInitDoesNotSnapshotSettings(unittest.TestCase):
    """``RuntimeState.__init__`` must not call ``get_settings()``.

    Tests construct ``RuntimeState()`` first and patch env (e.g.
    ``LLM_MODEL``) before calling ``ensure_initialised()``. If
    ``__init__`` snapshots Settings against the un-patched env, the
    cached snapshot shadows the later patch and ``ensure_initialised``
    sees the wrong values — historically 55 CI tests blew up this way
    when ``LLM_MODEL`` was unset on the runner."""

    def test_construct_with_empty_llm_model_then_initialise(self):
        # Force LLM_MODEL empty during __init__; if __init__ snapshots
        # Settings, the cache locks in an empty model.
        with patch.dict(os.environ, {"LLM_MODEL": ""}, clear=False):
            runtime = RuntimeState()

        # Patch a valid model + run init. Post-fix this succeeds; pre-fix
        # the cached snapshot wins and _parse_model_spec("") raises.
        env = {
            "DEFAULT_ROON_ZONE": "Living Room",
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "dummy-key",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("app.runtime.state.RoonConnection", _StubRoonConnection),
            patch("app.runtime.state._load_agent_skills", return_value=[]),
            patch(
                "app.runtime.state._format_agent_skills_for_prompt",
                return_value=("<available_skills />", ""),
            ),
        ):
            runtime.ensure_initialised()
            self.assertTrue(runtime.initialised)


class TestStopMarkerTitleWiredToPlayHistory(unittest.TestCase):
    """``settings.stop_marker_title`` must reach ``play_history``
    after ``ensure_initialised``. The store filters tracks matching
    this title out of the play history so a stop action doesn't
    pollute the deque — if the wiring breaks, customised marker
    titles (``ROON_STOP_MARKER_TITLE``) silently stop being
    filtered."""

    def test_custom_stop_marker_reaches_play_history(self):
        with patch.dict(os.environ, {"ROON_STOP_MARKER_TITLE": "custom_marker"}, clear=False):
            with _initialised_runtime() as runtime:
                self.assertEqual(
                    runtime.play_history._stop_marker_title, "custom_marker",
                )


if __name__ == "__main__":
    unittest.main()
