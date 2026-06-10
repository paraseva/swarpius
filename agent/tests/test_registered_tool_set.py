"""After ``ensure_initialised``, the tool registry contains the
expected set of tools — with ``web_search`` present iff a web-search
provider is configured.

Existing tests verify individual tool wiring (cross-wiring) and
that the registry is non-empty (init-partial-failure), but nothing
asserts on the exact tool-name set or on the web_search
conditional. A regression that dropped a registration or inverted
the conditional would only surface via an indirect end-to-end
failure.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _env_and_settings_cache(env: dict):
    from app.settings.core import reset_settings_for_tests
    with patch.dict(os.environ, env, clear=False):
        reset_settings_for_tests()
        try:
            yield
        finally:
            reset_settings_for_tests()


def _initialise_runtime():
    from app.runtime.state import RuntimeState
    rs = RuntimeState()
    with (
        patch("app.runtime.state.RoonConnection", MagicMock),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("", ""),
        ),
    ):
        rs.ensure_initialised()
    return rs


_BASE_ENV = {
    "LLM_MODEL": "dummy/dummy-model",
    "LLM_API_KEY_DUMMY": "k",
    "DEFAULT_ROON_ZONE": "Living Room",
}

_ALWAYS_REGISTERED = {
    "roon_search",
    "roon_action",
    "roon_status",
    "roon_config",
    "result_fetch",
}


class TestRegisteredToolSet(unittest.TestCase):

    def test_five_roon_and_result_tools_registered_when_web_search_disabled(self):
        env = {**_BASE_ENV, "WEB_SEARCH_PROVIDER": "none"}
        with _env_and_settings_cache(env):
            rs = _initialise_runtime()
        registered = set(rs.tool_registry.tool_names)
        self.assertEqual(registered, _ALWAYS_REGISTERED)
        self.assertNotIn("web_search", registered)

    def test_web_search_registered_when_searxng_configured(self):
        env = {
            **_BASE_ENV,
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://localhost:8081",
        }
        with _env_and_settings_cache(env):
            rs = _initialise_runtime()
        registered = set(rs.tool_registry.tool_names)
        self.assertEqual(registered, _ALWAYS_REGISTERED | {"web_search"})


if __name__ == "__main__":
    unittest.main()
