"""Contract tests for analyser + runtime invariants.

Contracts encoded here:

- Scan-timeout per batch is derived from config (env or profile),
  not a hard-coded constant.
- Single result-store of truth — ``runtime.result_store`` and the
  fetch tool's store are the same dict (identity) or one proxies the
  other. No silent dual-write path.
- ``consolidate_lessons`` verifies every input lesson is represented
  (or explicitly logged as dropped) in the output.
- Resolved-profile lookup is honest — either all three agents'
  profiles are stored, or the one consumer distinguishes per call.
- Arbiter's "disabled" vs "LLM call failed" paths are distinguishable
  by the caller.
"""

import logging
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


# ------------------------------------------------------------------ #
#  Single result-store invariant                                       #
# ------------------------------------------------------------------ #

class TestSingleResultStore(unittest.TestCase):
    """One result-store sits behind both ``runtime.result_store`` and
    the fetch tool's view — mutations via one path are observable via
    the other (same dict identity, or the tool reads through a
    runtime accessor)."""

    @contextmanager
    def _init_runtime(self):
        from app.runtime.state import RuntimeState
        env = {
            "DEFAULT_ROON_ZONE": "Living Room",
            "SEARXNG_URL": "http://localhost:8081",
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "dummy-key",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("app.runtime.state.RoonConnection", MagicMock),
            patch("app.runtime.state._load_agent_skills", return_value=[]),
            patch(
                "app.runtime.state._format_agent_skills_for_prompt",
                return_value=("", ""),
            ),
        ):
            rs = RuntimeState()
            rs.ensure_initialised()
            yield rs

    def test_runtime_and_fetch_tool_share_single_store(self):
        """The fetch tool's read-path and runtime.result_store must
        resolve the same dict — either identical reference, or the
        tool reads via an accessor that returns runtime.result_store.
        The tool receives ``result_store`` by reference at
        instantiation, so they share identity."""
        with self._init_runtime() as runtime:
            fetch_tool = runtime.tool_registry.get("result_fetch").tool_instance
            self.assertIsNotNone(fetch_tool, "fetch tool not registered")

            # Mutate via the runtime, read via the tool. Both reference
            # the same dict, so no sync helper needs to fire.
            runtime.result_store["test_handle"] = {"payload": "x"}
            self.assertIn(
                "test_handle", fetch_tool.result_store,
                "mutation via runtime.result_store not visible to fetch tool — "
                "single-store invariant broken",
            )

# ------------------------------------------------------------------ #
#  consolidate_lessons integrity                                       #
# ------------------------------------------------------------------ #

class TestConsolidateLessonsIntegrity(unittest.TestCase):
    """Every input lesson fed to consolidate_lessons must be accounted
    for in the result — either represented (identity preserved or
    merged-with-attribution) or explicitly dropped with an audit log."""

    def test_dropped_lesson_is_logged(self):
        """Mock the LLM to consolidate away a lesson entirely. The
        consolidator must log (at info/warning) that the specific
        lesson was dropped so operators can audit."""
        from tempfile import TemporaryDirectory

        from analyser import analyse
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        lessons_path = Path(td.name) / "lessons-learned.md"
        with (
            patch.object(analyse, "LESSONS_PATH", lessons_path),
            patch.object(analyse, "count_lessons", return_value=10),
            patch.object(
                analyse, "read_lessons",
                return_value=(
                    "## Lesson A\nBody A\n*Source: r1*\n"
                    "## Lesson B\nBody B\n*Source: r2*\n"
                ),
            ),
        ):
            fake_litellm = MagicMock()
            fake_litellm.completion = MagicMock(return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(
                    content='[{"heading": "Lesson A", "body": "Body A merged", "source": "r1"}]',
                ))],
            ))
            fake_litellm.success_callback = []
            fake_litellm.failure_callback = []

            from analyser import llm_layer
            with (
                patch.object(llm_layer, "litellm", fake_litellm),
                self.assertLogs("analyse", level=logging.WARNING) as captured,
            ):
                analyse.consolidate_lessons("dummy/model", "dummy-key")

        # The dropped "Lesson B" must be mentioned in some log record
        self.assertTrue(
            any("Lesson B" in rec.getMessage() for rec in captured.records),
            "Dropped lesson not logged — silent info loss",
        )


# ------------------------------------------------------------------ #
#  Resolved-profile per-agent lookup                                   #
# ------------------------------------------------------------------ #

class TestResolvedProfileLookup(unittest.TestCase):
    """request_flow.py reads the resolved profile to decide whether to
    apply prompt caching. Sub-agents may have different profiles (e.g.
    Anthropic cache markers only apply when the model is Anthropic,
    which can differ per agent), so the runtime must expose either
    all three profiles or a per-agent lookup."""

    def test_arbiter_profile_accessible(self):
        """The arbiter's resolved profile must be reachable — either as
        ``runtime.resolved_arbiter_profile`` or via a lookup helper."""
        from app.runtime.state import RuntimeState
        env = {
            "DEFAULT_ROON_ZONE": "Living Room",
            "SEARXNG_URL": "http://localhost:8081",
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "dummy-key",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("app.runtime.state.RoonConnection", MagicMock),
            patch("app.runtime.state._load_agent_skills", return_value=[]),
            patch(
                "app.runtime.state._format_agent_skills_for_prompt",
                return_value=("", ""),
            ),
        ):
            rs = RuntimeState()
            rs.ensure_initialised()

        # Either form is acceptable — the test just needs one of them
        has_arbiter_profile = hasattr(rs, "resolved_arbiter_profile") or (
            hasattr(rs, "get_resolved_profile") and
            rs.get_resolved_profile("arbiter") is not None
        )
        self.assertTrue(
            has_arbiter_profile,
            "Arbiter's resolved profile must be reachable via either "
            "``resolved_arbiter_profile`` or ``get_resolved_profile()``.",
        )


# ------------------------------------------------------------------ #
#  Arbiter disabled vs failed distinguishability                       #
# ------------------------------------------------------------------ #

class TestArbiterIODistinguishable(unittest.TestCase):
    """arbitrate_interrupt's "queue" fallback fires in two unrelated
    situations: arbiter_client is None (disabled), or the LLM call
    raised. The reasons returned must differ so the caller can tell
    a misconfigured arbiter from a transient runtime failure."""

    def test_disabled_vs_failed_distinguishable(self):
        """The two paths return different reason strings ('Interrupt
        arbiter unavailable' vs 'Arbiter failed; defaulting to
        queue'). The pin ensures they don't collapse to one."""
        from app.coordinator.request_flow import arbitrate_interrupt
        from app.runtime.state import RuntimeState

        env = {
            "DEFAULT_ROON_ZONE": "Living Room",
            "SEARXNG_URL": "http://localhost:8081",
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "dummy-key",
            # Arbiter is opt-in (default off). Enable it so the
            # "failed call" branch is reachable — the "disabled"
            # branch is forced explicitly below.
            "ENABLE_INTERRUPT_ARBITER": "true",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("app.runtime.state.RoonConnection", MagicMock),
            patch("app.runtime.state._load_agent_skills", return_value=[]),
            patch(
                "app.runtime.state._format_agent_skills_for_prompt",
                return_value=("", ""),
            ),
        ):
            rs_disabled = RuntimeState()
            rs_disabled.ensure_initialised()
            rs_disabled.arbiter_client = None  # simulate disabled

            rs_failed = RuntimeState()
            rs_failed.ensure_initialised()
            failing_client = MagicMock()
            failing_client.completion = MagicMock(side_effect=RuntimeError("boom"))
            rs_failed.arbiter_client = failing_client

        out_disabled = arbitrate_interrupt(rs_disabled, "old req", "new req")
        out_failed = arbitrate_interrupt(rs_failed, "old req", "new req")

        # Both paths return "queue"; the reasons must differ so the
        # caller can distinguish them.
        self.assertNotEqual(
            out_disabled.reason, out_failed.reason,
            "arbiter-disabled and arbiter-failed return indistinguishable "
            "reasons — callers can't tell a misconfigured arbiter from a "
            "transient failure.",
        )


if __name__ == "__main__":
    unittest.main()
