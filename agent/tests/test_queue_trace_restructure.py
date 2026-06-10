"""Tests for queue trace restructuring — flat text to per-zone arrays."""

import unittest

from tools.roon_status import RoonStatusTool

_restructure_queue_trace = RoonStatusTool._restructure_queue_trace


class TestRestructureQueueTrace(unittest.TestCase):

    def test_single_zone(self):
        text = (
            "Queue for MDAC+ USB (3 tracks)\n\n"
            "(1) [a1b2c] Track A | Album A | Artist A\n"
            "(2) [d3e4f] Track B | Album B | Artist B\n"
            "(3) [g5h6i] Track C | Album C | Artist C"
        )
        result = _restructure_queue_trace(text)
        self.assertEqual(len(result["queues"]), 1)
        q = result["queues"][0]
        self.assertEqual(q["zone"], "MDAC+ USB")
        self.assertEqual(len(q["items"]), 3)
        self.assertIn("[a1b2c]", q["items"][0])
        self.assertIn("[g5h6i]", q["items"][2])
        self.assertEqual(result["no_queue_zones"], [])

    def test_multiple_zones(self):
        text = (
            "Queue for RME (2 tracks)\n\n"
            "(1) [aaa11] Song X | Album X | Artist X\n"
            "(2) [bbb22] Song Y | Album Y | Artist Y\n\n"
            "Queue for MDAC+ USB (1 track)\n\n"
            "(1) [ccc33] Song Z | Album Z | Artist Z"
        )
        result = _restructure_queue_trace(text)
        self.assertEqual(len(result["queues"]), 2)
        self.assertEqual(result["queues"][0]["zone"], "RME")
        self.assertEqual(len(result["queues"][0]["items"]), 2)
        self.assertEqual(result["queues"][1]["zone"], "MDAC+ USB")
        self.assertEqual(len(result["queues"][1]["items"]), 1)

    def test_with_no_queue_zones(self):
        text = (
            "Queue for RME (1 track)\n\n"
            "(1) [aaa11] Song | Album | Artist\n\n"
            "No queue data: BTD 700, BT-W5 Akash"
        )
        result = _restructure_queue_trace(text)
        self.assertEqual(len(result["queues"]), 1)
        self.assertEqual(result["no_queue_zones"], ["BTD 700", "BT-W5 Akash"])

    def test_zone_label_with_alias(self):
        """Zone labels may include alias: 'Speakers (MDAC+ USB)'."""
        text = (
            "Queue for Speakers (MDAC+ USB) (2 tracks)\n\n"
            "(1) [aaa11] Song A | Album | Artist\n"
            "(2) [bbb22] Song B | Album | Artist"
        )
        result = _restructure_queue_trace(text)
        self.assertEqual(len(result["queues"]), 1)
        self.assertEqual(result["queues"][0]["zone"], "Speakers (MDAC+ USB)")
        self.assertEqual(len(result["queues"][0]["items"]), 2)

    def test_empty_result(self):
        result = _restructure_queue_trace("No queue data available for any zone")
        self.assertEqual(result["queues"], [])

    def test_singular_track(self):
        text = "Queue for Headphones (1 track)\n\n(1) [abc12] Only Song | Album | Artist"
        result = _restructure_queue_trace(text)
        self.assertEqual(len(result["queues"]), 1)
        self.assertEqual(len(result["queues"][0]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
