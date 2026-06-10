"""Tests for ``RoonPlaybackMixin`` volume helpers."""

import unittest
from typing import Optional
from unittest.mock import MagicMock

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


def _make_host_with_volume(min_=0, max_=100, step=1, value=0):
    from roon_core.playback import RoonPlaybackMixin

    class FakeHost(RoonPlaybackMixin):
        def __init__(self) -> None:
            self.api = MagicMock()
            self.api.outputs = {
                "out-1": {
                    "display_name": "Phone",
                    "volume": {
                        "min": min_, "max": max_, "step": step, "value": value,
                    },
                },
            }

        def _lookup_output_id_for_controls(
            self,
            zone: Optional[str] = None,
            output: Optional[str] = None,
        ) -> str:
            _ = (zone, output)
            return "out-1"

    return FakeHost()


class TestSetVolumeAbsolute(unittest.TestCase):
    def _make_host(self):
        from roon_core.playback import RoonPlaybackMixin

        class FakeHost(RoonPlaybackMixin):
            def __init__(self) -> None:
                self.api = MagicMock()
                self.api.outputs = {
                    "out-1": {
                        "volume": {"min": 0, "max": 100, "step": 1, "value": 0},
                    },
                }

            def _lookup_output_id_for_controls(
                self,
                zone: Optional[str] = None,
                output: Optional[str] = None,
            ) -> str:
                _ = (zone, output)
                return "out-1"

        return FakeHost()

    def test_calls_change_volume_raw_with_absolute_method(self):
        host = self._make_host()
        host.set_volume_absolute(volume=12, output="Phone")
        host.api.change_volume_raw.assert_called_once_with("out-1", 12, "absolute")

    def test_passes_value_through_unchanged_for_max_15_device(self):
        host = self._make_host()
        host.set_volume_absolute(volume=15, output="Phone")
        args, _kwargs = host.api.change_volume_raw.call_args
        self.assertEqual(args[1], 15)
        self.assertEqual(args[2], "absolute")


class TestSetVolumePercentReadback(unittest.TestCase):
    def test_returns_previous_and_achieved_for_max_100_device(self):
        host = _make_host_with_volume(min_=0, max_=100, step=1, value=30)
        result = host.set_volume_percent(volume=50, output="Living Room")
        self.assertEqual(result.previous_percent, 30)
        self.assertEqual(result.achieved_percent, 50)

    def test_returns_quantised_achieved_for_max_15_device(self):
        host = _make_host_with_volume(min_=0, max_=15, step=1, value=0)
        result = host.set_volume_percent(volume=50, output="Phone")
        self.assertEqual(result.previous_percent, 0)
        self.assertEqual(result.achieved_percent, 53)

    def test_returns_0_for_zero_request_on_quantised_device(self):
        host = _make_host_with_volume(min_=0, max_=15, step=1, value=0)
        result = host.set_volume_percent(volume=0, output="Phone")
        self.assertEqual(result.achieved_percent, 0)

    def test_calls_underlying_api_with_requested_percent(self):
        host = _make_host_with_volume(min_=0, max_=15, step=1, value=0)
        host.set_volume_percent(volume=50, output="Phone")
        host.api.set_volume_percent.assert_called_once_with(
            output_id="out-1", absolute_value=50,
        )


class TestChangeVolumePercentReadback(unittest.TestCase):
    def test_returns_previous_and_achieved_after_increase_on_max_100(self):
        host = _make_host_with_volume(min_=0, max_=100, step=1, value=50)
        result = host.change_volume_percent(delta=10, output="Living Room")
        self.assertEqual(result.previous_percent, 50)
        self.assertEqual(result.achieved_percent, 60)

    def test_returns_quantised_after_increase_on_max_15(self):
        host = _make_host_with_volume(min_=0, max_=15, step=1, value=8)
        result = host.change_volume_percent(delta=10, output="Phone")
        self.assertEqual(result.previous_percent, 53)
        self.assertEqual(result.achieved_percent, 67)

    def test_clamps_at_max_on_overflow(self):
        host = _make_host_with_volume(min_=0, max_=15, step=1, value=14)
        result = host.change_volume_percent(delta=50, output="Phone")
        self.assertEqual(result.achieved_percent, 100)

    def test_calls_underlying_api_with_requested_delta(self):
        host = _make_host_with_volume(min_=0, max_=100, step=1, value=50)
        host.change_volume_percent(delta=10, output="Living Room")
        host.api.change_volume_percent.assert_called_once_with(
            output_id="out-1", relative_value=10,
        )


if __name__ == "__main__":
    unittest.main()
