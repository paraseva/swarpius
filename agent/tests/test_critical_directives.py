"""extract_critical_directives — the <!-- critical --> extraction path that
feeds the high-attention 'Key Rules' prompt section; a regression here would
silently drop critical directives.

Contract: text between ``<!-- critical -->`` / ``<!-- /critical -->`` markers
is pulled into critical_text; the markers + their content are removed from
the returned cleaned body; surrounding prose is preserved; no marker leaves
the body unchanged.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.coordinator.skill_loader import extract_critical_directives  # noqa: E402


class TestExtractCriticalDirectives(unittest.TestCase):
    def test_no_marker_returns_body_unchanged(self):
        body = "Just some skill guidance.\nNo markers here."
        critical, cleaned = extract_critical_directives(body)
        self.assertEqual(critical, "")
        self.assertEqual(cleaned, body)

    def test_single_block_extracted_and_removed_from_body(self):
        body = (
            "Intro line.\n"
            "<!-- critical -->\n"
            "Always confirm the zone before playback.\n"
            "<!-- /critical -->\n"
            "Outro line."
        )
        critical, cleaned = extract_critical_directives(body)
        self.assertIn("Always confirm the zone before playback.", critical)
        self.assertNotIn("Always confirm the zone before playback.", cleaned)
        self.assertNotIn("<!-- critical", cleaned)
        self.assertIn("Intro line.", cleaned)
        self.assertIn("Outro line.", cleaned)

    def test_multiple_blocks_all_extracted(self):
        body = (
            "<!-- critical -->\n"
            "Rule one.\n"
            "<!-- /critical -->\n"
            "Middle prose.\n"
            "<!-- critical -->\n"
            "Rule two.\n"
            "<!-- /critical -->\n"
        )
        critical, cleaned = extract_critical_directives(body)
        self.assertIn("Rule one.", critical)
        self.assertIn("Rule two.", critical)
        self.assertIn("Middle prose.", cleaned)
        self.assertNotIn("Rule one.", cleaned)
        self.assertNotIn("Rule two.", cleaned)


if __name__ == "__main__":
    unittest.main()
