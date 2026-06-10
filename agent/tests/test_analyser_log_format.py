"""Tests for the passive analyser's log formatting.

Verifies that format_conversation_logs assembles the analyser payload
faithfully — the entire coordinator system prompt (all sections, in
coordinator order, with internal `## ` headings demoted to `##### `) is
included so the analyser sees exactly what the coordinator saw.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analyser.analyse import _format_coordinator_prompt, format_conversation_logs


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestFormatConversationLogs(unittest.TestCase):

    def _make_conversation(self, tmp: Path, *, system_prompt: str | None = None) -> Path:
        """Create a minimal conversation directory structure."""
        date_dir = tmp / "2026-03-31"
        conv_dir = date_dir / "c01"
        req_dir = conv_dir / "rq-c01-0001"
        req_dir.mkdir(parents=True)
        (req_dir / "tool_executions").mkdir()
        (req_dir / "coordinator_steps").mkdir()
        prompts_dir = req_dir / "prompts"
        prompts_dir.mkdir()

        _write_json(conv_dir / "conversation_summary.json", {
            "conversation_id": "c01",
            "topic_summary": "Test conversation",
        })
        _write_json(req_dir / "request.json", {
            "user_input": "play something",
            "timestamp": "2026-03-31T10:00:00",
        })
        _write_json(req_dir / "outcome.json", {
            "status": "completed",
            "total_steps": 1,
            "total_duration_ms": 500,
            "chat_response": "Done!",
        })

        if system_prompt:
            (prompts_dir / "coordinator_system.txt").write_text(
                system_prompt, encoding="utf-8",
            )

        return conv_dir

    def test_includes_static_prompt_sections(self):
        """All sections — including static ones (Behaviour, Skill
        Definitions, Key Rules) — are included so the analyser sees
        exactly what the coordinator saw."""
        system_prompt = (
            "You are Swarpius, a music assistant for Roon.\n"
            "\n"
            "## Behaviour\n"
            "- Be helpful.\n"
            "\n"
            "## Skill Definitions\n"
            "<available_skills>lots of text</available_skills>\n"
            "\n"
            "## Key Rules\n"
            "Always confirm zone changes.\n"
        )
        with TemporaryDirectory() as tmp:
            conv_dir = self._make_conversation(Path(tmp), system_prompt=system_prompt)
            output = format_conversation_logs(conv_dir)

        self.assertIn("Behaviour", output)
        self.assertIn("Be helpful.", output)
        self.assertIn("Skill Definitions", output)
        self.assertIn("available_skills", output)
        self.assertIn("Key Rules", output)
        self.assertIn("Always confirm zone changes.", output)

    def test_uses_full_prompt_section_label(self):
        """The system-prompt block is labelled with the 'full, exactly
        as the coordinator saw it' phrasing — the guide cites this
        label by name, so changes need to be coordinated."""
        with TemporaryDirectory() as tmp:
            conv_dir = self._make_conversation(
                Path(tmp), system_prompt="You are Swarpius.\n",
            )
            output = format_conversation_logs(conv_dir)

        self.assertIn(
            "#### Coordinator System Prompt (full, exactly as the coordinator saw it):",
            output,
        )

    def test_preserves_coordinator_section_order(self):
        """Sections appear in the output in the order the coordinator
        saw them — order is the only signal the analyser has for which
        sections are static prefix vs dynamic tail."""
        system_prompt = (
            "You are Swarpius.\n"
            "\n"
            "## Skill Definitions\n"
            "first\n"
            "\n"
            "## Current Time\n"
            "second\n"
            "\n"
            "## Zone Status\n"
            "third\n"
            "\n"
            "## Key Rules\n"
            "fourth\n"
        )
        with TemporaryDirectory() as tmp:
            conv_dir = self._make_conversation(Path(tmp), system_prompt=system_prompt)
            output = format_conversation_logs(conv_dir)

        skills_pos = output.index("Skill Definitions")
        time_pos = output.index("Current Time")
        zone_pos = output.index("Zone Status")
        rules_pos = output.index("Key Rules")
        self.assertLess(skills_pos, time_pos)
        self.assertLess(time_pos, zone_pos)
        self.assertLess(zone_pos, rules_pos)

    def test_no_system_prompt_file_still_works(self):
        """Missing prompts/coordinator_system.txt doesn't break formatting."""
        with TemporaryDirectory() as tmp:
            conv_dir = self._make_conversation(Path(tmp), system_prompt=None)
            output = format_conversation_logs(conv_dir)

        self.assertIn("play something", output)
        self.assertIn("Done!", output)

    def test_empty_dynamic_sections_still_appear(self):
        """Empty sections (e.g. `## Execution Trace\\n[]`) still appear
        in the output — they tell the analyser the coordinator had no
        prior context, which is useful signal."""
        system_prompt = (
            "You are Swarpius.\n"
            "\n"
            "## Execution Trace\n"
            "[]\n"
        )
        with TemporaryDirectory() as tmp:
            conv_dir = self._make_conversation(Path(tmp), system_prompt=system_prompt)
            output = format_conversation_logs(conv_dir)

        self.assertIn("Execution Trace", output)


class TestFormatCoordinatorPrompt(unittest.TestCase):
    """`_format_coordinator_prompt` demotes the system prompt's `## `
    headings to `##### ` so they nest cleanly inside the payload's
    own section structure without colliding with payload headers."""

    def test_demotes_h2_to_h5(self):
        prompt = "## Zone Status\nKitchen playing\n"
        result = _format_coordinator_prompt(prompt)
        self.assertIn("##### Zone Status", result)
        self.assertNotIn("## Zone Status", result.replace("##### Zone Status", ""))

    def test_demotes_all_h2_occurrences(self):
        prompt = (
            "## First\n"
            "body one\n"
            "## Second\n"
            "body two\n"
            "## Third\n"
            "body three\n"
        )
        result = _format_coordinator_prompt(prompt)
        self.assertEqual(result.count("##### "), 3)

    def test_only_affects_h2_headings_not_deeper(self):
        """Existing `### ` and `#### ` headings inside the prompt must
        be left alone — only `## ` is the level that would clash with
        payload-level headers."""
        prompt = (
            "## Outer\n"
            "### Inner\n"
            "#### Deeper\n"
            "##### Deepest\n"
        )
        result = _format_coordinator_prompt(prompt)
        self.assertIn("##### Outer", result)
        self.assertIn("### Inner", result)
        self.assertIn("#### Deeper", result)
        self.assertIn("##### Deepest", result)

    def test_preserves_non_heading_content(self):
        """Body content (including lines that mention `##` inline) is
        unchanged."""
        prompt = (
            "## Behaviour\n"
            "Use ## to mark section headers.\n"
            "Example: `## My section`\n"
        )
        result = _format_coordinator_prompt(prompt)
        self.assertIn("Use ## to mark section headers.", result)
        self.assertIn("Example: `## My section`", result)

    def test_no_h2_headings_returns_unchanged(self):
        prompt = "You are Swarpius.\n\nNo sections here.\n"
        self.assertEqual(_format_coordinator_prompt(prompt), prompt)


if __name__ == "__main__":
    unittest.main()
