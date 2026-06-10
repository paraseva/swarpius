"""Tests for strip_roon_links() — Roon [[ID|Name]] markup removal."""

import unittest

from roon_core.browse import strip_roon_links


class TestStripRoonLinks(unittest.TestCase):

    def test_single_link(self):
        self.assertEqual(strip_roon_links("[[1069690|Pat Benatar]]"), "Pat Benatar")

    def test_multiple_links(self):
        text = "[[1069690|Pat Benatar]] & [[5563258|Neil Giraldo]]"
        self.assertEqual(strip_roon_links(text), "Pat Benatar & Neil Giraldo")

    def test_complex_multi_artist(self):
        text = "[[2119774|Mr. Sam]] & [[9924190|Andy Duguid]] vs. [[1069690|Pat Benatar]]"
        self.assertEqual(strip_roon_links(text), "Mr. Sam & Andy Duguid vs. Pat Benatar")

    def test_plain_text_unchanged(self):
        self.assertEqual(strip_roon_links("Pat Benatar"), "Pat Benatar")

    def test_accented_characters_preserved(self):
        self.assertEqual(strip_roon_links("[[999|Renée Fleming]]"), "Renée Fleming")

    def test_apostrophe_in_name(self):
        self.assertEqual(
            strip_roon_links("[[888|Jack Russell's Great White]]"),
            "Jack Russell's Great White",
        )

    def test_empty_string(self):
        self.assertEqual(strip_roon_links(""), "")

    def test_mixed_plain_and_linked(self):
        text = "Pat Benatar, [[5563258|Neil Giraldo]]"
        self.assertEqual(strip_roon_links(text), "Pat Benatar, Neil Giraldo")


if __name__ == "__main__":
    unittest.main()
