import asyncio
import unittest
from typing import Any, Dict, List, Optional

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from tools.roon_status import (  # noqa: E402
    RoonStatusTool,
    RoonStatusToolConfig,
    RoonStatusToolInputSchema,
)


class FakeRoonConnection:
    def get_zone_snapshot(self, zone: Optional[str] = None) -> Dict[str, Any]:
        _ = zone
        return {
            "display_name": "Living Room",
            "state": "playing",
            "queue_items_remaining": 12,
            "seek_position": 85,
            "settings": {"shuffle": True, "loop": "loop"},
            "outputs": [
                {
                    "output_id": "output-1",
                    "display_name": "Speaker 1",
                    "volume": {"type": "number", "value": 45, "is_muted": False,
                               "min": 0, "max": 100, "step": 1},
                },
            ],
            "now_playing": {
                "length": 260,
                "three_line": {
                    "line1": "Track A",
                    "line2": "Artist A",
                    "line3": "Album A",
                },
            },
        }

    def get_zones_snapshot(self) -> List[Dict[str, Any]]:
        return [
            {
                "display_name": "Living Room",
                "state": "playing",
                "zone_id": "zone-1",
                "seek_position": 85,
                "settings": {"shuffle": True, "loop": "loop"},
                "outputs": [
                    {
                        "output_id": "output-1",
                        "display_name": "Speaker 1",
                        "volume": {"type": "number", "value": 45, "is_muted": False,
                                   "min": 0, "max": 100, "step": 1},
                    },
                ],
                "now_playing": {
                    "length": 260,
                    "three_line": {
                        "line1": "Track A",
                        "line2": "Artist A",
                        "line3": "Album A",
                    },
                },
            },
            {
                "display_name": "Kitchen",
                "state": "stopped",
                "zone_id": "zone-2",
                "outputs": [
                    {
                        "output_id": "output-2",
                        "display_name": "Speaker 2",
                    },
                ],
            },
        ]

    def get_queue_snapshot(self, zone: Optional[str] = None) -> Dict[str, Any]:
        _ = zone
        return {
            "zone_id": "zone-1",
            "display_name": "Living Room",
            "state": "playing",
            "queue_items_remaining": 12,
            "latest_queue_event": {"type": "queue", "zone_id": "zone-1"},
        }

    def get_queue_items(self, zone: Optional[str] = None) -> List[Dict[str, Any]]:
        # Kitchen (stopped) has no queue
        if zone == "Kitchen":
            return []
        return [
            {
                "queue_item_id": 100,
                "length": 200,
                "image_key": "img_100",
                "two_line": {"line1": "Track A", "line2": "Artist A"},
                "three_line": {"line1": "Track A", "line2": "Artist A", "line3": "Album A"},
            },
        ]

    def get_queue_references(self, zone=None):
        return None

    def get_default_zone(self):
        return "Living Room"


class TestRoonStatusTool(unittest.TestCase):
    def _tool(self) -> RoonStatusTool:
        tool = RoonStatusTool(config=RoonStatusToolConfig(resolve_zone=lambda z: z))
        tool.roon_connection = FakeRoonConnection()
        return tool

    def test_get_zones_status_compact_format(self):
        output = asyncio.run(
            self._tool().run_async(RoonStatusToolInputSchema(operation="get_zones_status")),
        )
        self.assertIn("Living Room", output.result)
        self.assertIn("Kitchen", output.result)
        self.assertIn("Track A", output.result)
        self.assertIn("Shuffle: on", output.result)
        self.assertIn("Repeat: loop", output.result)
        self.assertIn("45%", output.result)

    def test_get_zones_status_includes_seek(self):
        output = asyncio.run(
            self._tool().run_async(RoonStatusToolInputSchema(operation="get_zones_status")),
        )
        self.assertIn("Position: 1:25 / 4:20", output.result)

    def test_get_zones_status_all_zones_when_no_zone_specified(self):
        output = asyncio.run(
            self._tool().run_async(RoonStatusToolInputSchema(operation="get_zones_status")),
        )
        self.assertIn("Living Room", output.result)
        self.assertIn("Kitchen", output.result)

    def test_get_zones_status_single_zone_when_specified(self):
        output = asyncio.run(
            self._tool().run_async(
                RoonStatusToolInputSchema(operation="get_zones_status", zone="Living Room"),
            ),
        )
        self.assertIn("Living Room", output.result)
        self.assertNotIn("Kitchen", output.result)

    def test_get_zones_status_fixed_volume(self):
        output = asyncio.run(
            self._tool().run_async(RoonStatusToolInputSchema(operation="get_zones_status")),
        )
        self.assertIn("not controllable (fixed)", output.result)

    def test_get_queue_status_single_zone(self):
        output = asyncio.run(
            self._tool().run_async(
                RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
            ),
        )
        self.assertIn("Track A", output.result)
        self.assertRegex(output.result, r"\[Q:[0-9a-f]{5}\]")
        self.assertIn("1 track)", output.result)
        self.assertEqual(output.zone, "Living Room")

    def test_get_queue_status_all_zones(self):
        """Omitting zone fetches all zones' queues."""
        output = asyncio.run(
            self._tool().run_async(RoonStatusToolInputSchema(operation="get_queue_status")),
        )
        self.assertIn("Queue for Living Room", output.result)
        self.assertIn("Track A", output.result)
        # Kitchen has no queue — should be listed in "no queue data"
        self.assertIn("Kitchen", output.result)
        self.assertIn("No queue data", output.result)

    def test_get_queue_status_all_zones_caches_each(self):
        """All-zones fetch caches display block for each zone with a queue."""
        cache = {}
        tool = RoonStatusTool(config=RoonStatusToolConfig(
            resolve_zone=lambda z: z,
            roon_connection=FakeRoonConnection(),
            queue_display_cache=cache,
        ))
        asyncio.run(
            tool.run_async(RoonStatusToolInputSchema(operation="get_queue_status")),
        )
        self.assertIn("Living Room", cache)
        self.assertIn("<list>", cache["Living Room"])
        # Kitchen has no queue — should not be cached
        self.assertNotIn("Kitchen", cache)

    def test_queue_display_cache_shared_reference(self):
        """Cache dict passed to config must be the same object — not a Pydantic copy."""
        cache = {}
        tool = RoonStatusTool(config=RoonStatusToolConfig(
            resolve_zone=lambda z: z,
            roon_connection=FakeRoonConnection(),
            queue_display_cache=cache,
        ))
        asyncio.run(
            tool.run_async(
                RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
            ),
        )
        self.assertTrue(len(cache) > 0, "Cache dict was not populated — Pydantic may have copied it")
        self.assertIn("<list>", cache["Living Room"])
        self.assertIn("Track A", cache["Living Room"])


if __name__ == "__main__":
    unittest.main()
