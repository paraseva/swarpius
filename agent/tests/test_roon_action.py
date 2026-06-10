"""Action tool tests covering library actions, transport settings, and
volume/control APIs.
"""

import asyncio
import unittest
from typing import List

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.schemas import RoonCoreItemSummarySchema  # noqa: E402

try:
    from tests._browse_fake import BrowseFake, make_action_tool
except ModuleNotFoundError:
    from _browse_fake import BrowseFake, make_action_tool

from tools.roon_action import (  # noqa: E402
    RoonActionTool,
    RoonActionToolInputSchema,
    RoonActionToolOutputSchema,
)

# Album-shaped action list — matches what real albums expose. Tests
# pick actions from this list, so anything they need (Play Now, Queue,
# Add Next, Shuffle) must be present.
_ALBUM_ACTIONS = ["Play Now", "Add Next", "Queue", "Shuffle", "Start Radio"]


def _fake_with_items(refs: List[str]) -> BrowseFake:
    fake = BrowseFake()
    for ref in refs:
        fake.register_item(ref, f"Track {ref}", action_titles=_ALBUM_ACTIONS)
    return fake


def _make_tool(fake: BrowseFake) -> RoonActionTool:
    return make_action_tool(fake)


class TestRoonActionExecutionOrder(unittest.TestCase):
    """Assert which (action, ref) pairs the tool dispatches, in order.
    The dispatch sequence is the observable contract — what gets sent
    to Roon for each item."""

    def _item(self, reference: str, title: str) -> RoonCoreItemSummarySchema:
        return RoonCoreItemSummarySchema(title=title, reference=reference)

    def _run(
        self, action: str, refs: List[str],
    ) -> tuple[BrowseFake, RoonActionToolOutputSchema]:
        fake = _fake_with_items(refs)
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(
            action=action,
            items=[self._item(r, f"Track {r}") for r in refs],
        )
        outcome = asyncio.run(tool.run_async(params))
        return fake, outcome

    def test_add_next_dispatches_single_item(self):
        fake, _ = self._run("Add Next", ["AAAAA"])
        self.assertEqual(fake.dispatched_actions, [("Add Next", "AAAAA")])

    def test_queue_processes_items_in_original_order(self):
        fake, _ = self._run("Queue", ["AAAAA", "BBBBB", "CCCCC"])
        self.assertEqual(
            fake.dispatched_actions,
            [("Queue", "AAAAA"), ("Queue", "BBBBB"), ("Queue", "CCCCC")],
        )

    def test_play_now_multi_item_rectifies_to_queue(self):
        """Per the matrix: Play Now with >1 items silently rectifies
        to Queue. The queue's auto-start-on-idle covers the
        "play the first one" UX without special sequencing."""
        fake, _ = self._run("Play Now", ["AAAAA", "BBBBB", "CCCCC"])
        self.assertEqual(
            fake.dispatched_actions,
            [("Queue", "AAAAA"), ("Queue", "BBBBB"), ("Queue", "CCCCC")],
        )

    def test_queue_auto_starts_playback_when_zone_is_stopped(self):
        fake = _fake_with_items(["AAAAA", "BBBBB"])
        fake.zone_state = "stopped"
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(
            action="Queue",
            zone="Living Room",
            items=[
                self._item("AAAAA", "Track AAAAA"),
                self._item("BBBBB", "Track BBBBB"),
            ],
        )
        outcome = asyncio.run(tool.run_async(params))

        # Behaviour: queue then dispatch play to the zone.
        self.assertEqual(
            fake.playback_calls,
            [{"control": "play", "zone": "Living Room"}],
        )
        self.assertIn("playback started", outcome.result.lower())

    def test_queue_does_not_auto_start_when_zone_is_already_playing(self):
        fake = _fake_with_items(["AAAAA"])
        fake.zone_state = "playing"
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(
            action="Queue",
            zone="Living Room",
            items=[self._item("AAAAA", "Track AAAAA")],
        )
        asyncio.run(tool.run_async(params))
        self.assertEqual(fake.playback_calls, [])

    def test_play_now_does_not_trigger_extra_play_even_when_stopped(self):
        fake = _fake_with_items(["AAAAA"])
        fake.zone_state = "stopped"
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(
            action="Play Now",
            zone="Living Room",
            items=[self._item("AAAAA", "Track AAAAA")],
        )
        asyncio.run(tool.run_async(params))
        self.assertEqual(fake.playback_calls, [])

    def test_shuffle_randomises_then_play_now_plus_queue(self):
        fake, _ = self._run("Shuffle", ["AAAAA", "BBBBB", "CCCCC"])
        actions = fake.dispatched_actions
        self.assertEqual(len(actions), 3)
        # First item is Play Now (seeds playback), the rest are Queue.
        self.assertEqual(actions[0][0], "Play Now")
        self.assertEqual([a for a, _ in actions[1:]], ["Queue", "Queue"])
        # All 3 refs are covered (some random order).
        self.assertEqual(
            sorted(ref for _, ref in actions),
            ["AAAAA", "BBBBB", "CCCCC"],
        )


class TestRoonPlaybackSettingsExecution(unittest.TestCase):
    def test_set_shuffle_calls_connection(self):
        fake = BrowseFake()
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(action="set_shuffle", shuffle=True)
        asyncio.run(tool.run_async(params))
        self.assertEqual(fake.shuffle_calls, [True])

    def test_set_repeat_calls_connection(self):
        fake = BrowseFake()
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(action="set_repeat", repeat="loop_one")
        asyncio.run(tool.run_async(params))
        self.assertEqual(fake.repeat_calls, ["loop_one"])

    def test_seek_calls_connection(self):
        fake = BrowseFake()
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(
            action="seek", seconds=42, seek_method="relative",
        )
        asyncio.run(tool.run_async(params))
        self.assertEqual(
            fake.seek_calls, [{"seconds": 42, "method": "relative"}],
        )


class TestRoonAdvancedControlExecution(unittest.TestCase):
    def _run(self, **kwargs) -> BrowseFake:
        fake = BrowseFake()
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(**kwargs)
        asyncio.run(tool.run_async(params))
        return fake

    def test_get_volume(self):
        fake = self._run(action="get_volume", zone="Living Room")
        self.assertEqual(fake.volume_get_calls, 1)

    def test_volume_and_mute_controls(self):
        fake = self._run(action="set_volume", zone="Living Room", volume=30)
        self.assertEqual(fake.set_volume_calls, [30])
        fake = self._run(action="change_volume", zone="Living Room", delta=-5)
        self.assertEqual(fake.change_volume_calls, [-5])
        fake = self._run(action="mute", zone="Living Room")
        self.assertEqual(fake.mute_calls, [True])
        fake = self._run(action="unmute", zone="Living Room")
        self.assertEqual(fake.mute_calls, [False])

    def test_global_and_group_controls(self):
        fake = self._run(action="pause_all")
        self.assertEqual(fake.pause_all_calls, 1)
        fake = self._run(action="standby", zone="Living Room")
        self.assertEqual(fake.standby_calls, 1)
        fake = self._run(action="convenience_switch", zone="Living Room")
        self.assertEqual(fake.convenience_switch_calls, 1)


class TestVolumeReadbackSurfacing(unittest.TestCase):
    """The roon_action result string surfaces previous → achieved on
    every successful volume action, baking per-zone history into the
    execution trace and exposing quantisation when it occurs."""

    def _run_with_result(self, set_result=None, change_result=None, **kwargs):
        from roon_core.playback import VolumeChangeResult
        fake = BrowseFake()
        if set_result is not None:
            prev, ach = set_result
            fake.set_volume_percent = lambda **_: VolumeChangeResult(prev, ach)
        if change_result is not None:
            prev, ach = change_result
            fake.change_volume_percent = lambda **_: VolumeChangeResult(prev, ach)
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(**kwargs)
        return asyncio.run(tool.run_async(params))

    def test_set_volume_reports_previous_and_achieved_when_quantised(self):
        outcome = self._run_with_result(
            set_result=(0, 53), action="set_volume", zone="Phone", volume=50,
        )
        self.assertIn("0% → 53%", outcome.result)
        self.assertIn("quantises", outcome.result)
        # Requested value isn't restated — it's already in the trace
        # input field (volume: 50). Only the causal hint is new.
        self.assertNotIn("requested", outcome.result)

    def test_set_volume_reports_previous_and_achieved_when_match(self):
        outcome = self._run_with_result(
            set_result=(30, 50), action="set_volume", zone="Living Room", volume=50,
        )
        self.assertIn("30% → 50%", outcome.result)
        self.assertNotIn("quantises", outcome.result)

    def test_change_volume_reports_previous_and_achieved(self):
        outcome = self._run_with_result(
            change_result=(53, 67), action="change_volume", zone="Phone", delta=10,
        )
        self.assertIn("53% → 67%", outcome.result)


class TestFixedVolumeReporting(unittest.TestCase):
    """Volume actions on a fixed-volume output should fail loudly with
    a descriptive error, not silently report success. Mute is exempt —
    most fixed-volume outputs still accept mute via the transport
    feature."""

    def _run_with_fixed_volume(self, **kwargs) -> RoonActionToolOutputSchema:
        from app.exceptions import FixedVolumeError
        fake = BrowseFake()

        def _raise_fixed(*args, **_kwargs):
            raise FixedVolumeError(
                "Output 'Phone' has fixed volume and cannot be controlled "
                "(level is set on the device itself, not via Roon).",
            )

        fake.set_volume_percent = _raise_fixed
        fake.change_volume_percent = _raise_fixed
        fake.get_volume_percent = _raise_fixed

        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(**kwargs)
        return asyncio.run(tool.run_async(params))

    def test_set_volume_reports_fixed_volume_error(self):
        outcome = self._run_with_fixed_volume(
            action="set_volume", zone="Phone", volume=50,
        )
        self.assertIn("FAILED", outcome.result)
        self.assertIsNotNone(outcome.error)
        self.assertIn("fixed volume", outcome.error.lower())

    def test_change_volume_reports_fixed_volume_error(self):
        outcome = self._run_with_fixed_volume(
            action="change_volume", zone="Phone", delta=10,
        )
        self.assertIn("FAILED", outcome.result)
        self.assertIsNotNone(outcome.error)
        self.assertIn("fixed volume", outcome.error.lower())

    def test_get_volume_reports_fixed_volume_error(self):
        outcome = self._run_with_fixed_volume(action="get_volume", zone="Phone")
        self.assertIn("FAILED", outcome.result)
        self.assertIsNotNone(outcome.error)
        self.assertIn("fixed volume", outcome.error.lower())

    def test_mute_still_works_on_fixed_volume_output(self):
        # Default mute is unmodified — it should be unaffected by the
        # FixedVolumeError patches on the level-controlling methods.
        fake = BrowseFake()
        tool = _make_tool(fake)
        params = RoonActionToolInputSchema(action="mute", zone="Phone")
        outcome = asyncio.run(tool.run_async(params))
        self.assertIn("SUCCESSFUL", outcome.result)
        self.assertEqual(fake.mute_calls, [True])


if __name__ == "__main__":
    unittest.main()
