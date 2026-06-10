"""Tests for sanitise_for_tts — parity with frontend sanitiseTtsText.

Mirrors web-client/src/utils/sanitiseTtsText.test.ts plus backend-specific
cases (details blocks, emojis, smart truncation).
"""

import unittest

from app.coordinator.sanitise import sanitise_for_tts


class TestMarkdownStripping(unittest.TestCase):
    """Markdown formatting should be stripped, keeping text content."""

    def test_bold_italic(self):
        self.assertEqual(sanitise_for_tts("This is ***bold italic*** text"), "This is bold italic text")

    def test_inline_code(self):
        self.assertEqual(sanitise_for_tts("Run `npm install` now"), "Run npm install now")

    def test_fenced_code_block(self):
        result = sanitise_for_tts("Hello.\n```js\nconsole.log(\"hi\")\n```\nDone.")
        self.assertNotIn("```", result or "")
        self.assertIn("Hello.", result)

    def test_headings(self):
        self.assertEqual(sanitise_for_tts("## Heading\nSome text"), "Heading\nSome text")

    def test_links(self):
        self.assertEqual(
            sanitise_for_tts("Check [this link](https://example.com) out"),
            "Check this link out",
        )

    def test_bullet_dash(self):
        self.assertEqual(
            sanitise_for_tts("- First item\n- Second item"),
            "First item\nSecond item",
        )

    def test_numbered_list(self):
        self.assertEqual(
            sanitise_for_tts("1. First\n2. Second\n10. Tenth"),
            "First\nSecond\nTenth",
        )

    def test_blockquote(self):
        self.assertEqual(sanitise_for_tts("> quoted text"), "quoted text")

    def test_horizontal_rule(self):
        result = sanitise_for_tts("Above\n---\nBelow")
        self.assertIn("Above", result)
        self.assertIn("Below", result)
        self.assertNotIn("---", result)

    def test_arrow_to_comma(self):
        self.assertEqual(
            sanitise_for_tts("**Lounge** → Living Room"),
            "Lounge, Living Room",
        )

    def test_combined_markdown(self):
        text = "## Results\n\n- **Track 1** by *Artist A*\n- **Track 2** by *Artist B*\n\n> Enjoy!"
        result = sanitise_for_tts(text)
        self.assertIn("Track 1 by Artist A", result)
        self.assertIn("Enjoy!", result)
        self.assertNotIn("##", result)
        self.assertNotIn("**", result)
        self.assertNotIn("-", result.split("\n")[0] if result.startswith("-") else "ok")


class TestBackendSpecific(unittest.TestCase):
    """Backend-specific: details blocks, emojis."""

    def test_list_block_stripped(self):
        text = (
            "Here are the playlists.\n\n"
            "<list><summary>Playlists (3 items)</summary>\n\n"
            "1. Favourites — 28 Tracks\n"
            "2. Favourites 2 — 13 Tracks\n"
            "3. Favourites_Qobuz — 57 Tracks\n"
            "</list>"
        )
        result = sanitise_for_tts(text)
        self.assertEqual(result, "Here are the playlists.")
        self.assertNotIn("<list>", result)
        self.assertNotIn("Favourites", result)
        self.assertNotIn("28 Tracks", result)

    def test_unknown_future_tag_stripped(self):
        text = "Intro.\n<table><row>A</row><row>B</row></table>\nOutro."
        result = sanitise_for_tts(text)
        self.assertIn("Intro.", result)
        self.assertIn("Outro.", result)
        self.assertNotIn("<table>", result)
        self.assertNotIn("<row>", result)
        self.assertNotIn("A", result.replace("Outro.", "").replace("Intro.", ""))

    def test_nested_same_name_tags_stripped(self):
        text = (
            "Album results.\n"
            "<list><summary>Album (4 tracks, 2 discs)</summary>\n\n"
            "<list><summary>Disc 1 (2 tracks)</summary>\n\n"
            "1. Track A\n2. Track B\n</list>\n\n"
            "<list><summary>Disc 2 (2 tracks)</summary>\n\n"
            "1. Track C\n2. Track D\n</list>\n\n"
            "</list>"
        )
        result = sanitise_for_tts(text)
        self.assertEqual(result, "Album results.")
        self.assertNotIn("Track", result)
        self.assertNotIn("<list>", result)
        self.assertNotIn("</list>", result)
        self.assertNotIn("<summary>", result)

    def test_self_closing_tag_stripped(self):
        text = "Before <tag attr=\"x\"/> after."
        result = sanitise_for_tts(text)
        self.assertNotIn("<", result)
        self.assertIn("Before", result)
        self.assertIn("after.", result)

    def test_emojis_stripped(self):
        result = sanitise_for_tts("Playing 🎵 some music 🎶")
        self.assertNotIn("🎵", result)
        self.assertNotIn("🎶", result)
        self.assertIn("Playing", result)


class TestSmartTruncation(unittest.TestCase):
    """Long responses should be truncated to the first sentence."""

    def test_short_response_not_truncated(self):
        text = "Now playing Bohemian Rhapsody by Queen."
        self.assertEqual(sanitise_for_tts(text), text)

    def test_long_response_truncated_to_first_sentence(self):
        text = "Now playing Bohemian Rhapsody by Queen. " + "Here is some additional detail. " * 20
        result = sanitise_for_tts(text)
        self.assertEqual(result, "Now playing Bohemian Rhapsody by Queen.")

    def test_no_sentence_boundary_returns_none(self):
        text = "A" * 400  # long, no sentence boundary
        result = sanitise_for_tts(text)
        self.assertIsNone(result)


class TestEdgeCases(unittest.TestCase):

    def test_empty_string(self):
        self.assertIsNone(sanitise_for_tts(""))

    def test_none(self):
        self.assertIsNone(sanitise_for_tts(None))

    def test_whitespace_collapses(self):
        result = sanitise_for_tts("Line one\n\n\n\nLine two")
        self.assertEqual(result, "Line one\n\nLine two")

    def test_trims(self):
        self.assertEqual(sanitise_for_tts("  hello  "), "hello")
