"""Tests for queue reference lifecycle management.

Covers QueueReferenceMap (unit), events.py integration (reference minting
on subscription events), roon_status (pre-assigned refs), and roon_action
(resolution from ref map instead of result store).
"""

import asyncio
import unittest
from unittest.mock import MagicMock

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.queue_references import QueueReferenceMap, _item_description

# ── Fixtures ────────────────────────────────────────────────────────


def _raw_queue_item(queue_item_id, title, artist, album, length=200):
    return {
        "queue_item_id": queue_item_id,
        "length": length,
        "image_key": f"img_{queue_item_id}",
        "one_line": {"line1": f"{title} - {artist}"},
        "two_line": {"line1": title, "line2": artist},
        "three_line": {"line1": title, "line2": artist, "line3": album},
    }


SAMPLE_ITEMS = [
    _raw_queue_item(83581, "I'm Good (Blue)", "David Guetta", "I'm Good (Blue)"),
    _raw_queue_item(83536, "Cry For You", "September", "Club Anthems"),
    _raw_queue_item(83552, "Tell Me", "INNA", "E.T."),
    _raw_queue_item(83568, "Believe", "Cher", "Believe"),
]


# ══════════════════════════════════════════════════════════════════════
# QueueReferenceMap — unit tests
# ══════════════════════════════════════════════════════════════════════


class TestMint(unittest.TestCase):

    def test_creates_5_char_hex(self):
        ref_map = QueueReferenceMap()
        ref = ref_map.mint(100)
        self.assertEqual(len(ref), 5)
        self.assertTrue(all(c in "0123456789abcdef" for c in ref))

    def test_same_item_returns_same_ref(self):
        ref_map = QueueReferenceMap()
        ref1 = ref_map.mint(100)
        ref2 = ref_map.mint(100)
        self.assertEqual(ref1, ref2)

    def test_different_items_get_different_refs(self):
        ref_map = QueueReferenceMap()
        refs = {ref_map.mint(i) for i in range(50)}
        self.assertEqual(len(refs), 50)

    def test_bidirectional_mapping(self):
        ref_map = QueueReferenceMap()
        ref = ref_map.mint(42)
        self.assertEqual(ref_map.get_ref(42), ref)
        qid, err = ref_map.resolve(ref)
        self.assertEqual(qid, 42)
        self.assertIsNone(err)


class TestInvalidate(unittest.TestCase):

    def test_removes_from_active(self):
        ref_map = QueueReferenceMap()
        ref = ref_map.mint(100)
        ref_map.invalidate(100, "Track A")
        self.assertIsNone(ref_map.get_ref(100))
        self.assertNotIn(ref, ref_map.active_refs.values())

    def test_moves_to_invalidated_set(self):
        ref_map = QueueReferenceMap()
        ref = ref_map.mint(100)
        ref_map.invalidate(100, "Track A")
        qid, err = ref_map.resolve(ref)
        self.assertIsNone(qid)
        self.assertIn("removed", err)
        self.assertIn("Track A", err)

    def test_invalidate_unknown_item_is_noop(self):
        ref_map = QueueReferenceMap()
        ref_map.invalidate(999)  # should not raise

    def test_invalidated_set_bounded(self):
        ref_map = QueueReferenceMap()
        # Mint and invalidate more than the max
        for i in range(250):
            ref_map.mint(i)
            ref_map.invalidate(i, f"track-{i}")
        self.assertLessEqual(len(ref_map._invalidated), 200)


class TestResolve(unittest.TestCase):

    def test_valid_reference(self):
        ref_map = QueueReferenceMap()
        ref = ref_map.mint(100)
        qid, err = ref_map.resolve(ref)
        self.assertEqual(qid, 100)
        self.assertIsNone(err)

    def test_invalidated_reference(self):
        ref_map = QueueReferenceMap()
        ref = ref_map.mint(100)
        ref_map.invalidate(100, "Believe")
        qid, err = ref_map.resolve(ref)
        self.assertIsNone(qid)
        self.assertIn("removed", err)
        self.assertIn("Believe", err)

    def test_unknown_reference(self):
        ref_map = QueueReferenceMap()
        qid, err = ref_map.resolve("fffff")
        self.assertIsNone(qid)
        self.assertIn("Unknown", err)


class TestClear(unittest.TestCase):

    def test_clears_all(self):
        ref_map = QueueReferenceMap()
        ref_map.mint(1)
        ref_map.mint(2)
        ref_map.invalidate(2, "x")
        ref_map.clear()
        self.assertEqual(ref_map.active_refs, {})
        self.assertIsNone(ref_map.get_ref(1))
        _, err = ref_map.resolve("anything")
        self.assertIn("Unknown", err)


class TestReconcileFullList(unittest.TestCase):

    def test_initial_list_mints_all(self):
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        self.assertEqual(len(ref_map.active_refs), 4)
        for item in SAMPLE_ITEMS:
            self.assertIsNotNone(ref_map.get_ref(item["queue_item_id"]))

    def test_preserves_existing_refs(self):
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        original_refs = dict(ref_map.active_refs)

        # Reconcile with the same list — refs should not change
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        self.assertEqual(ref_map.active_refs, original_refs)

    def test_invalidates_removed_items(self):
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        removed_ref = ref_map.get_ref(83568)  # "Believe"

        # New list without "Believe"
        new_items = SAMPLE_ITEMS[:3]
        ref_map.reconcile_full_list(new_items, old_items=SAMPLE_ITEMS)

        self.assertIsNone(ref_map.get_ref(83568))
        qid, err = ref_map.resolve(removed_ref)
        self.assertIsNone(qid)
        self.assertIn("removed", err)
        self.assertIn("Believe", err)

    def test_mints_for_new_arrivals(self):
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS[:2])
        self.assertEqual(len(ref_map.active_refs), 2)

        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        self.assertEqual(len(ref_map.active_refs), 4)

    def test_full_replacement(self):
        """Simulates "Play Now" replacing the entire queue."""
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        old_refs = dict(ref_map.active_refs)

        new_items = [_raw_queue_item(99999, "New Track", "Artist", "Album")]
        ref_map.reconcile_full_list(new_items, old_items=SAMPLE_ITEMS)

        # All old items invalidated, one new item minted
        self.assertEqual(len(ref_map.active_refs), 1)
        self.assertIsNotNone(ref_map.get_ref(99999))
        for old_qid, old_ref in old_refs.items():
            _, err = ref_map.resolve(old_ref)
            self.assertIn("removed", err)


class TestApplyInsertsAndRemoves(unittest.TestCase):

    def test_apply_inserts(self):
        ref_map = QueueReferenceMap()
        ref_map.apply_inserts(SAMPLE_ITEMS[:2])
        self.assertEqual(len(ref_map.active_refs), 2)

    def test_apply_inserts_idempotent(self):
        ref_map = QueueReferenceMap()
        ref_map.apply_inserts(SAMPLE_ITEMS[:2])
        original = dict(ref_map.active_refs)
        ref_map.apply_inserts(SAMPLE_ITEMS[:2])
        self.assertEqual(ref_map.active_refs, original)

    def test_apply_removes(self):
        ref_map = QueueReferenceMap()
        ref_map.apply_inserts(SAMPLE_ITEMS)
        removed_ref = ref_map.get_ref(83536)

        ref_map.apply_removes([SAMPLE_ITEMS[1]])  # "Cry For You"
        self.assertIsNone(ref_map.get_ref(83536))
        qid, err = ref_map.resolve(removed_ref)
        self.assertIsNone(qid)
        self.assertIn("Cry For You", err)

    def test_apply_removes_preserves_others(self):
        ref_map = QueueReferenceMap()
        ref_map.apply_inserts(SAMPLE_ITEMS)
        ref_map.apply_removes([SAMPLE_ITEMS[0]])
        self.assertEqual(len(ref_map.active_refs), 3)


class TestItemDescription(unittest.TestCase):

    def test_uses_two_line(self):
        item = _raw_queue_item(1, "My Track", "My Artist", "My Album")
        self.assertEqual(_item_description(item), "My Track")

    def test_falls_back_to_one_line(self):
        item = {"queue_item_id": 1, "one_line": {"line1": "Track - Artist"}}
        self.assertEqual(_item_description(item), "Track - Artist")

    def test_empty_item(self):
        self.assertEqual(_item_description({}), "")


# ══════════════════════════════════════════════════════════════════════
# Events integration — refs minted/invalidated on subscription events
# ══════════════════════════════════════════════════════════════════════


from roon_core.events import RoonEventsMixin


class FakeEventsHost(RoonEventsMixin):
    """Minimal host for testing RoonEventsMixin with queue references."""

    def __init__(self):
        self.api = MagicMock()
        self.api.zones = {
            "zone-1": {"display_name": "Living Room", "zone_id": "zone-1"},
        }
        self.api._roonsocket = MagicMock()
        self.target_zone = "Living Room"
        self._event_listeners = []
        self._subscriptions_registered = False
        self._queue_subscribed_zones = set()
        self._queue_socket_id = None
        self._queue_items_cache = {}
        self._queue_ref_maps = {}
        self.last_state_event = None
        self.last_queue_event = None
        self.last_queue_events_by_zone = {}


class TestEventsQueueRefIntegration(unittest.TestCase):

    def test_full_list_event_creates_refs(self):
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        ref_map = host._queue_ref_maps.get("zone-1")
        self.assertIsNotNone(ref_map)
        self.assertEqual(len(ref_map.active_refs), 4)

    def test_repeated_full_list_preserves_refs(self):
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        original = dict(host._queue_ref_maps["zone-1"].active_refs)

        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        self.assertEqual(host._queue_ref_maps["zone-1"].active_refs, original)

    def test_differential_insert_mints_ref(self):
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        self.assertEqual(len(host._queue_ref_maps["zone-1"].active_refs), 4)

        new_track = _raw_queue_item(99999, "New Track", "New Artist", "New Album")
        host._on_queue_event({"zone_id": "zone-1", "changes": [
            {"operation": "insert", "index": 2, "items": [new_track]},
        ]})
        ref_map = host._queue_ref_maps["zone-1"]
        self.assertEqual(len(ref_map.active_refs), 5)
        self.assertIsNotNone(ref_map.get_ref(99999))

    def test_differential_remove_invalidates_ref(self):
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        ref_map = host._queue_ref_maps["zone-1"]
        removed_ref = ref_map.get_ref(83581)  # "I'm Good (Blue)"

        host._on_queue_event({"zone_id": "zone-1", "changes": [
            {"operation": "remove", "index": 0, "count": 1},
        ]})
        self.assertEqual(len(ref_map.active_refs), 3)
        qid, err = ref_map.resolve(removed_ref)
        self.assertIsNone(qid)
        self.assertIn("removed", err)
        self.assertIn("I'm Good (Blue)", err)

    def test_zone_removal_clears_refs(self):
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        self.assertIn("zone-1", host._queue_ref_maps)

        host._queue_subscribed_zones.add("zone-1")
        host._on_state_event("zones_removed", ["zone-1"])
        self.assertNotIn("zone-1", host._queue_ref_maps)

    def test_surviving_refs_still_valid_after_remove(self):
        """Items that remain in the queue keep their original references."""
        host = FakeEventsHost()
        host._on_queue_event({"zone_id": "zone-1", "items": list(SAMPLE_ITEMS)})
        ref_map = host._queue_ref_maps["zone-1"]
        surviving_ref = ref_map.get_ref(83536)  # "Cry For You" at index 1

        # Remove index 0 (first item)
        host._on_queue_event({"zone_id": "zone-1", "changes": [
            {"operation": "remove", "index": 0, "count": 1},
        ]})

        # "Cry For You" should still resolve with same reference
        self.assertEqual(ref_map.get_ref(83536), surviving_ref)
        qid, err = ref_map.resolve(surviving_ref)
        self.assertEqual(qid, 83536)
        self.assertIsNone(err)


# ══════════════════════════════════════════════════════════════════════
# roon_status integration — queue uses pre-assigned refs
# ══════════════════════════════════════════════════════════════════════


from tools.roon_status import RoonStatusTool, RoonStatusToolConfig, RoonStatusToolInputSchema


class FakeRoonConnectionForQueueRefs:
    """Fake RoonConnection that provides queue items and reference maps."""

    def __init__(self, queue_items=None, zone_id="zone-1"):
        self._queue_items = queue_items or []
        self._zone_id = zone_id
        self._queue_ref_maps = {}
        self._zone = {
            "display_name": "Living Room",
            "zone_id": zone_id,
            "state": "playing",
            "outputs": [{"display_name": "Living Room", "output_id": f"output-{zone_id}"}],
        }

        # Mint refs for initial items
        if self._queue_items:
            ref_map = QueueReferenceMap()
            ref_map.reconcile_full_list(self._queue_items)
            self._queue_ref_maps[zone_id] = ref_map

    def get_zone_snapshot(self, zone=None):
        return self._zone

    def get_zones_snapshot(self):
        return [self._zone]

    def get_queue_items(self, zone=None):
        return self._queue_items

    def get_queue_references(self, zone=None):
        return self._queue_ref_maps.get(self._zone_id)


class TestQueueStatusPreAssignedRefs(unittest.TestCase):

    def _tool(self, queue_items=None):
        conn = FakeRoonConnectionForQueueRefs(queue_items=queue_items)
        tool = RoonStatusTool(config=RoonStatusToolConfig(
            resolve_zone=lambda z: z,
            roon_connection=conn,
        ))
        tool.roon_connection = conn
        return tool, conn

    def test_uses_preassigned_refs(self):
        tool, conn = self._tool(queue_items=SAMPLE_ITEMS)
        ref_map = conn.get_queue_references()
        expected_ref = ref_map.get_ref(83581)

        output = asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        self.assertIn(expected_ref, output.result)

    def test_repeated_fetch_returns_same_refs(self):
        tool, conn = self._tool(queue_items=SAMPLE_ITEMS)

        out1 = asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        out2 = asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        # Extract the bracketed references from both outputs
        import re
        refs1 = re.findall(r"\[Q:([0-9a-f]{5})\]", out1.result)
        refs2 = re.findall(r"\[Q:([0-9a-f]{5})\]", out2.result)
        self.assertEqual(refs1, refs2)

    def test_no_result_store_writes(self):
        """Queue status should not call store_handle."""
        store_calls = []

        def spy_store(payload):
            store_calls.append(payload)
            return "que_00001"

        conn = FakeRoonConnectionForQueueRefs(queue_items=SAMPLE_ITEMS)
        tool = RoonStatusTool(config=RoonStatusToolConfig(
            resolve_zone=lambda z: z,
            roon_connection=conn,
            store_handle=spy_store,
        ))
        tool.roon_connection = conn

        asyncio.run(tool.run_async(
            RoonStatusToolInputSchema(operation="get_queue_status", zone="Living Room"),
        ))
        self.assertEqual(len(store_calls), 0, "Queue status should not write to result store")


# ══════════════════════════════════════════════════════════════════════
# roon_action integration — resolve from ref map
# ══════════════════════════════════════════════════════════════════════


class FakeRoonConnectionForAction:
    """Fake RoonConnection for testing play_from_here resolution."""

    def __init__(self, ref_map=None):
        self.play_from_here_calls = []
        self._queue_ref_maps = {"zone-1": ref_map} if ref_map else {}
        self.api = MagicMock()
        self.api.zones = {
            "z1": {
                "display_name": "Living Room",
                "zone_id": "z1",
                "outputs": [{"output_id": "o1", "display_name": "Living Room"}],
            },
        }
        self.api.outputs = {
            "o1": {"output_id": "o1", "display_name": "Living Room", "zone_id": "z1"},
        }

    def play_from_here(self, queue_item_id, zone=None):
        self.play_from_here_calls.append({"queue_item_id": queue_item_id, "zone": zone})

    def resolve_queue_ref(self, hex_ref, zone=None):
        """Search all zone ref maps for the reference."""
        for ref_map in self._queue_ref_maps.values():
            qid, err = ref_map.resolve(hex_ref)
            if qid is not None:
                return qid
            if err and "removed" in err:
                raise ValueError(err)
        raise ValueError(f"Queue reference '{hex_ref}' not found. Fetch the queue first.")


class TestActionQueueRefResolution(unittest.TestCase):

    def _run(self, queue_ref=None, queue_item_id=None, ref_map=None, zone=None):
        from tools.roon_action import (
            RoonActionTool,
            RoonActionToolConfig,
            RoonActionToolInputSchema,
        )

        conn = FakeRoonConnectionForAction(ref_map=ref_map)
        tool = RoonActionTool(config=RoonActionToolConfig(
            resolve_zone=lambda z: z,
            roon_connection=conn,
        ))
        tool.roon_connection = conn
        params = RoonActionToolInputSchema(
            action="play_from_here",
            queue_item_id=queue_item_id,
            queue_ref=queue_ref,
            zone=zone,
        )
        output = asyncio.run(tool.run_async(params))
        return output, conn

    def test_resolves_valid_ref_from_map(self):
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        ref = ref_map.get_ref(83581)

        output, conn = self._run(queue_ref=ref, zone="Living Room", ref_map=ref_map)
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(conn.play_from_here_calls[0]["queue_item_id"], 83581)

    def test_invalidated_ref_returns_informative_error(self):
        ref_map = QueueReferenceMap()
        ref_map.reconcile_full_list(SAMPLE_ITEMS)
        ref = ref_map.get_ref(83581)
        ref_map.invalidate(83581, "I'm Good (Blue)")

        output, conn = self._run(queue_ref=ref, zone="Living Room", ref_map=ref_map)
        self.assertIn("FAILED", output.result)
        self.assertIn("removed", output.error.lower())
        self.assertEqual(len(conn.play_from_here_calls), 0)

    def test_unknown_ref_returns_error(self):
        ref_map = QueueReferenceMap()
        output, conn = self._run(queue_ref="zzzzz", zone="Living Room", ref_map=ref_map)
        self.assertIn("FAILED", output.result)
        self.assertEqual(len(conn.play_from_here_calls), 0)


if __name__ == "__main__":
    unittest.main()
