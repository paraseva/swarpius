"""Tests for <queue zone="..."/> tag expansion in response extraction.

Covers tag parsing, zone alias/group resolution, cache lookup,
and fallback behaviour.
"""

import unittest

from app.roon.tag_expansion import expand_queue_tags


class TestExpandQueueTags(unittest.TestCase):
    """Test <queue zone="..."/> tag expansion."""

    def _cache(self):
        return {
            "Headphones": (
                '<list><summary>Queue for Headphones (3 tracks)</summary>\n\n'
                '(1) [bfb97] Light My Fire | Clubland 90s | Club House\n'
                '(2) [a00dc] Show Me Love | Clubland 90s | Robin S\n'
                '(3) [89d18] I Like To Move It | Clubland 90s | Reel 2 Real\n'
                '</list>'
            ),
            "Living Room": (
                '<list><summary>Queue for Living Room (1 track)</summary>\n\n'
                '(1) [ff001] Bohemian Rhapsody | Greatest Hits | Queen\n'
                '</list>'
            ),
        }

    def _resolve(self, zone: str) -> str:
        """Simple resolver: case-insensitive match + alias map."""
        aliases = {"cans": "Headphones", "lounge": "Living Room"}
        normalised = zone.strip().lower()
        # Check aliases first
        for alias, display in aliases.items():
            if normalised == alias:
                return display
        # Case-insensitive display name match
        for name in ["Headphones", "Living Room"]:
            if normalised == name.lower():
                return name
        raise ValueError(f"Unknown zone '{zone}'")

    # ── Basic expansion ──

    def test_basic_expansion(self):
        text = '<queue zone="Headphones"/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("<list>", result)
        self.assertIn("</list>", result)
        self.assertIn("Light My Fire", result)
        self.assertIn("Queue for Headphones (3 tracks)", result)

    def test_tag_with_space_before_slash(self):
        text = '<queue zone="Headphones" />'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("<list>", result)
        self.assertIn("Light My Fire", result)

    # ── Zone resolution ──

    def test_alias_resolved(self):
        text = '<queue zone="cans"/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("Queue for Headphones", result)
        self.assertIn("Light My Fire", result)

    def test_case_insensitive_zone(self):
        text = '<queue zone="headphones"/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("Queue for Headphones", result)

    def test_different_zone(self):
        text = '<queue zone="lounge"/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("Queue for Living Room", result)
        self.assertIn("Bohemian Rhapsody", result)

    # ── Surrounding text ──

    def test_text_preserved(self):
        text = 'Here is the queue:\n\n<queue zone="Headphones"/>\n\nEnjoy!'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("Here is the queue:", result)
        self.assertIn("Enjoy!", result)
        self.assertIn("<list>", result)

    # ── Cache miss / error ──

    def test_unknown_zone_produces_error(self):
        text = '<queue zone="Nonexistent"/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("not available", result)
        self.assertIn("Nonexistent", result)
        self.assertNotIn("<list>", result)

    def test_zone_not_in_cache_produces_error(self):
        """Zone resolves but wasn't queried this request."""
        cache = {}  # empty cache
        text = '<queue zone="Headphones"/>'
        result = expand_queue_tags(text, cache, self._resolve)
        self.assertIn("not available", result)
        self.assertNotIn("<list>", result)

    def test_empty_zone_produces_error(self):
        text = '<queue zone=""/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("Queue zone not specified", result)

    # ── Passthrough ──

    def test_no_tag_passthrough(self):
        text = "No queue tags here."
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertEqual(result, text)

    # ── Multiple tags ──

    def test_multiple_tags(self):
        text = '<queue zone="Headphones"/>\n\n<queue zone="Living Room"/>'
        result = expand_queue_tags(text, self._cache(), self._resolve)
        self.assertIn("Queue for Headphones", result)
        self.assertIn("Queue for Living Room", result)
        self.assertIn("Light My Fire", result)
        self.assertIn("Bohemian Rhapsody", result)
        self.assertEqual(result.count("<list>"), 2)


if __name__ == "__main__":
    unittest.main()
