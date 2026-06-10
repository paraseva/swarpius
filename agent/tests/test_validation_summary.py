"""CLI-mode boot-validation summary rendering.

The web UI renders validation from the broadcast payload; CLI users get
this text equivalent. Only the coordinator gates startup, so the summary
must treat sub-agent / backend failures as *degraded*, not fatal.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cli.validation_summary import degraded_items, format_summary  # noqa: E402
from app.settings.validation import (  # noqa: E402
    AgentResult,
    BackendResult,
    ValidationState,
    ValidationStatus,
)


def _status(results, backends, state=ValidationState.PASSED):
    return ValidationStatus(state=state, results=results, backends=backends)


class TestDegradedItems(unittest.TestCase):
    def test_excludes_coordinator_disabled_and_ok_rows(self):
        results = [
            AgentResult("coordinator", True, "anthropic",
                        "anthropic/claude-x", False, ok=False,
                        error_kind="auth_failed"),
            AgentResult("arbiter", True, "openai", "openai/gpt-5", False,
                        ok=False, error_kind="auth_failed", detail="bad key"),
            AgentResult("diagnostic", False, None, None, False),  # disabled
            AgentResult("analyser", True, "anthropic",
                        "anthropic/claude-x", True, ok=True),  # passing
        ]
        backends = [
            BackendResult("tts", "F5-TTS server", ok=False,
                          error_kind="network", detail="unreachable"),
            BackendResult("web-search", "SearXNG", ok=True),
        ]
        items = degraded_items(_status(results, backends))
        labels = [i.label for i in items]
        # Only the failed sub-agent + the failed backend.
        self.assertEqual(len(items), 2)
        self.assertTrue(any("arbiter" in label for label in labels))
        self.assertTrue(any("F5-TTS" in label for label in labels))
        self.assertFalse(any("coordinator" in label for label in labels))
        self.assertFalse(any("SearXNG" in label for label in labels))
        self.assertFalse(any("diagnostic" in label for label in labels))


class TestFormatSummary(unittest.TestCase):
    def test_flags_degraded_when_a_backend_is_down(self):
        results = [AgentResult("coordinator", True, "anthropic",
                               "anthropic/claude-x", False, ok=True)]
        backends = [BackendResult("tts", "F5-TTS server", ok=False,
                                  error_kind="network", detail="unreachable")]
        out = format_summary(_status(results, backends))
        self.assertIn("reduced capabilities", out)
        self.assertIn("✗", out)
        self.assertIn("F5-TTS", out)

    def test_ready_when_all_ok(self):
        results = [AgentResult("coordinator", True, "anthropic",
                               "anthropic/claude-x", False, ok=True)]
        backends = [BackendResult("web-search", "SearXNG", ok=True)]
        out = format_summary(_status(results, backends))
        self.assertIn("ready", out.lower())
        self.assertNotIn("reduced", out.lower())

    def test_elapsed_time_shown_in_header(self):
        results = [AgentResult("coordinator", True, "anthropic",
                               "anthropic/claude-x", False, ok=True)]
        out = format_summary(_status(results, []), elapsed=1.23)
        self.assertIn("1.2s", out)


if __name__ == "__main__":
    unittest.main()
