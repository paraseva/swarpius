"""Mixin-level queue-reference resolution: RoonEventsMixin.resolve_queue_ref
and get_queue_references.

Contract, from the queue-reference design: a minted 5-char ref resolves to
its queue_item_id, searching across zones; a ref whose item was removed,
and an unknown ref, each raise an informative error; get_queue_references
returns a zone's map.

Setup stubs only the boundary the mixin's composing class provides (the
per-zone ``_queue_ref_maps`` store + ``target_zone``); refs are populated
via the ``QueueReferenceMap`` API.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from roon_core.events import RoonEventsMixin  # noqa: E402


class _Events(RoonEventsMixin):
    def __init__(self):
        self._queue_ref_maps = {}
        self.target_zone = None


class TestResolveQueueRef(unittest.TestCase):
    def setUp(self):
        self.events = _Events()

    def test_minted_ref_resolves_to_queue_item_id(self):
        ref = self.events._get_or_create_ref_map("zone-1").mint(42)
        self.assertEqual(self.events.resolve_queue_ref(ref), 42)

    def test_resolves_a_ref_held_in_a_second_zone(self):
        self.events._get_or_create_ref_map("zone-1").mint(1)
        ref2 = self.events._get_or_create_ref_map("zone-2").mint(2)
        self.assertEqual(self.events.resolve_queue_ref(ref2), 2)

    def test_removed_item_ref_raises_informative_error(self):
        ref_map = self.events._get_or_create_ref_map("zone-1")
        ref = ref_map.mint(42)
        ref_map.invalidate(42, "Some Song")
        with self.assertRaises(ValueError) as cm:
            self.events.resolve_queue_ref(ref)
        self.assertIn("removed", str(cm.exception).lower())

    def test_unknown_ref_raises(self):
        with self.assertRaises(ValueError):
            self.events.resolve_queue_ref("zzzzz")

    def test_get_queue_references_returns_the_zone_map(self):
        ref_map = self.events._get_or_create_ref_map("zone-1")
        self.assertIs(self.events.get_queue_references("zone-1"), ref_map)


if __name__ == "__main__":
    unittest.main()
