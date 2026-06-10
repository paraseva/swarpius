"""Tests for RuntimeState behaviours: result store wiring, search history,
skill loading, config parsing, and chat text sanitisation.

Tests that exercise RuntimeState.ensure_initialised() use a shared helper
(_init_runtime) that patches out external dependencies (LLM framework,
Roon connection, instructor). When the LLM framework changes, only that
helper needs updating — individual test assertions stay the same.
"""

import asyncio
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.coordinator.sanitise import sanitise_agent_chat_text
from app.coordinator.skill_docs import AgentSkillDocument, AgentSkillMetadata  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402
from tools.result_fetch import ResultFetchToolInputSchema  # noqa: E402

# ------------------------------------------------------------------ #
#  Shared helpers for initialising RuntimeState without real deps     #
# ------------------------------------------------------------------ #

# Minimal fakes — satisfy the interfaces that initialise() calls
# without importing any framework module.

class _FakeApi:
    def __init__(self):
        self.zones = {}


class _FakeRoonConnection:
    def __init__(self, *args, **kwargs):
        _ = (args, kwargs)
        self.api = _FakeApi()

    def register_event_listener(self, listener):
        _ = listener

    def get_default_zone(self):
        return None


@contextmanager
def _init_runtime(extra_env=None, skill_docs=None):
    """Context manager that creates and initialises a RuntimeState with
    all external dependencies patched out.

    Yields the initialised RuntimeState. Only external deps are patched
    (Roon connection, skill loader). LLMClient is created normally
    (it doesn't connect until .completion() is called).
    """
    runtime = RuntimeState()
    env = {
        "DEFAULT_ROON_ZONE": "Living Room",
        "SEARXNG_URL": "http://localhost:8081",
        "LLM_MODEL": "dummy/dummy-model",
        "LLM_API_KEY_DUMMY": "dummy-key",
    }
    if extra_env:
        env.update(extra_env)

    with (
        patch.dict(os.environ, env, clear=False),
        patch("app.runtime.state.RoonConnection", _FakeRoonConnection),
        patch("app.runtime.state._load_agent_skills", return_value=skill_docs or []),
        patch("app.runtime.state._format_agent_skills_for_prompt", return_value=("<available_skills />", "")),
    ):
        runtime.ensure_initialised()
        yield runtime


# ------------------------------------------------------------------ #
#  Tests: chat text sanitisation (no framework deps)                 #
# ------------------------------------------------------------------ #

class TestSanitizeAgentChatText(unittest.TestCase):
    def test_removes_structured_leak_suffix(self):
        raw = (
            'Queued your tracks. \\"awaiting_user_response\\">false, '
            '\\"selected_skill\\":\\"roon_action\\"'
        )
        self.assertEqual(sanitise_agent_chat_text(raw), "Queued your tracks.")

    def test_extracts_chat_response_from_json_payload(self):
        raw = (
            '{"chat_response":"I found two versions. Which one do you want?",'
            '"awaiting_user_response":true,"selected_skill":null}'
        )
        self.assertEqual(
            sanitise_agent_chat_text(raw),
            "I found two versions. Which one do you want?",
        )


# ------------------------------------------------------------------ #
#  Tests: result store wiring (no framework deps)                    #
# ------------------------------------------------------------------ #

class TestResultStoreSync(unittest.TestCase):
    def test_store_result_handle_populates_runtime_store(self):
        """store_result_handle mints a handle and populates the runtime
        store; the fetch tool reads the same dict by reference, so no
        dual-write is needed."""
        runtime = RuntimeState()
        handle = runtime.store_result_handle(["one", "two"])

        self.assertEqual(handle, "res_00001")
        self.assertIn(handle, runtime.result_store)
        self.assertEqual(runtime.result_store[handle], ["one", "two"])


# ------------------------------------------------------------------ #
#  Tests: initialise() behaviours (framework-coupled setup)          #
# ------------------------------------------------------------------ #

class TestRuntimeInitialize(unittest.TestCase):
    """Tests that verify post-initialise() state.

    These use _init_runtime() which patches framework deps. If a test
    fails after the framework swap, update _init_runtime — not the
    assertions here.
    """

    def test_result_fetch_shares_runtime_result_store(self):
        """After init, the ResultFetchTool uses the same store as RuntimeState,
        so storing a handle makes it retrievable via the tool."""
        with _init_runtime() as runtime:
            pass

        fetch_tool = runtime.tool_registry.get("result_fetch").tool_instance
        self.assertIs(fetch_tool.result_store, runtime.result_store)

        handle = runtime.store_result_handle(["alpha", "beta", "gamma"])
        output = asyncio.run(
            fetch_tool.run_async(
                ResultFetchToolInputSchema(result_handle=handle),
            ),
        )
        self.assertEqual(output.result, "Cached list retrieved")
        self.assertEqual(output.items, ["alpha", "beta", "gamma"])

    def test_skills_loaded_and_formatted(self):
        """Skill docs are loaded from disk and formatted into the
        skills_provider for prompt injection."""
        skill_docs = [
            AgentSkillDocument(
                metadata=AgentSkillMetadata(
                    name="roon-action",
                    description="Control playback.",
                    location="/tmp/skills/roon-action/SKILL.md",
                ),
                body="## Guidance\nPass references together.",
            ),
            AgentSkillDocument(
                metadata=AgentSkillMetadata(
                    name="roon-search",
                    description="Search the library.",
                    location="/tmp/skills/roon-search/SKILL.md",
                ),
                body="## Guidance\nReferences stay valid.",
            ),
        ]
        with _init_runtime(skill_docs=skill_docs) as runtime:
            pass

        # Skills provider should have the formatted content
        self.assertEqual(runtime.skills_provider.get_info(), "<available_skills />")
        # System should be marked as initialised
        self.assertTrue(runtime.initialised)


# ------------------------------------------------------------------ #
#  Tests: search history (no framework deps)                         #
# ------------------------------------------------------------------ #

class TestSearchHistory(unittest.TestCase):
    def test_shows_entry_after_store(self):
        """After storing a result entry, the search history provider
        renders the handle, source label, item count, and fetch hint."""
        from app.runtime.result_store_types import ResultStoreEntry

        runtime = RuntimeState()
        runtime.store_result_entries([ResultStoreEntry(
            items=[{"title": f"Result {i}", "url": f"https://example.com/{i}",
                    "query": "artist x discography"} for i in range(7)],
            description='"artist x discography"',
            item_count=7,
            tool_name="web_search",
        )])
        runtime.set_prompt_state_context()

        info = runtime.search_history_provider.get_info()
        self.assertIn("res_00001", info)
        self.assertIn("Web:", info)
        self.assertIn("7 items", info)
        self.assertIn("result_fetch", info)

    def test_empty_when_no_searches(self):
        runtime = RuntimeState()
        runtime.set_prompt_state_context()
        self.assertEqual(runtime.search_history_provider.get_info(), "")


class TestLookupReferenceTitle(unittest.TestCase):
    """_lookup_reference_title finds item titles in the result store."""

    def test_finds_title_in_grouped_items(self):
        runtime = RuntimeState()
        runtime.result_store["res_00001"] = [
            {"group": "-", "items": [
                {"title": "Album X", "reference": "abc"},
                {"title": "Album Y", "reference": "def"},
            ]},
        ]
        self.assertEqual(runtime._lookup_reference_title("res_00001", "def"), "Album Y")

    def test_finds_title_in_flat_items(self):
        runtime = RuntimeState()
        runtime.result_store["res_00001"] = [
            {"title": "Track 1", "reference": "t1"},
            {"title": "Track 2", "reference": "t2"},
        ]
        self.assertEqual(runtime._lookup_reference_title("res_00001", "t2"), "Track 2")

    def test_returns_none_for_missing_reference(self):
        runtime = RuntimeState()
        runtime.result_store["res_00001"] = [
            {"group": "-", "items": [{"title": "X", "reference": "abc"}]},
        ]
        self.assertIsNone(runtime._lookup_reference_title("res_00001", "zzz"))

    def test_returns_none_for_missing_handle(self):
        runtime = RuntimeState()
        self.assertIsNone(runtime._lookup_reference_title("res_99999", "abc"))


# ------------------------------------------------------------------ #
#  Tests: result_fetch shares search_history after initialise()       #
# ------------------------------------------------------------------ #

class TestResultFetchSearchHistoryWiring(unittest.TestCase):
    def test_result_fetch_shares_search_history(self):
        """After init, the ResultFetchTool references the same search_history
        list as RuntimeState."""
        with _init_runtime() as runtime:
            pass

        fetch_tool = runtime.tool_registry.get("result_fetch").tool_instance
        self.assertIs(fetch_tool.search_history, runtime.search_history)

    def test_result_fetch_label_reflects_search_history(self):
        """End-to-end: storing a result entry populates history, then
        result_fetch includes the source label."""
        from app.runtime.result_store_types import ResultStoreEntry

        with _init_runtime() as runtime:
            pass

        runtime.store_result_entries([ResultStoreEntry(
            items=[{"url": "https://example.com/1", "title": "Result 1",
                    "content": "Snippet 1", "query": "best albums 2025"}],
            description='"best albums 2025"',
            item_count=1,
            tool_name="web_search",
        )])

        handle = runtime.search_history[0].result_handle
        fetch_tool = runtime.tool_registry.get("result_fetch").tool_instance
        fetch_output = asyncio.run(
            fetch_tool.run_async(ResultFetchToolInputSchema(result_handle=handle)),
        )
        self.assertEqual(fetch_output.result, 'List for: "best albums 2025"')


if __name__ == "__main__":
    unittest.main()
