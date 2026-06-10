from typing import List, Literal, NamedTuple, Optional

from app.exceptions import FixedVolumeError, ZoneLookupError


class VolumeChangeResult(NamedTuple):
    previous_percent: int
    achieved_percent: int


class RoonPlaybackMixin:
    """Playback half of :class:`RoonConnection`. Not a standalone mixin —
    lives in its own module for navigability, composed only into
    :class:`RoonConnection` alongside the other Roon* mixins. Delegates
    zone resolution to :class:`RoonZoneMixin` and reads queue caches
    populated by :class:`RoonEventsMixin`."""

    def playback_control(self, control: str, zone: Optional[str] = None) -> None:
        output_id = self._lookup_output_id(zone)
        self.api.playback_control(output_id, control=control)

    def set_shuffle(self, shuffle: bool, zone: Optional[str] = None) -> None:
        output_id = self._lookup_output_id(zone)
        self.api.shuffle(output_id, shuffle=shuffle)

    def set_repeat(
        self,
        repeat: Literal["disabled", "loop", "loop_one"],
        zone: Optional[str] = None,
    ) -> None:
        output_id = self._lookup_output_id(zone)
        self.api.repeat(output_id, repeat=repeat)

    def seek(
        self,
        seconds: int,
        method: Literal["absolute", "relative"] = "absolute",
        zone: Optional[str] = None,
    ) -> None:
        output_id = self._lookup_output_id(zone)
        self.api.seek(output_id, seconds=seconds, method=method)

    def _raise_if_fixed_volume(self, output_id: str) -> None:
        output = self.api.outputs.get(output_id) or {}
        if output.get("volume") is None:
            display = output.get("display_name") or output_id
            raise FixedVolumeError(
                f"Output '{display}' has fixed volume and cannot be controlled "
                f"(level is set on the device itself, not via Roon).",
            )

    def _volume_metadata(self, output_id: str) -> tuple:
        """Return (min, max, step, factor, current_raw) for the output's
        volume, or ``None`` if the output has fixed volume."""
        volume = (self.api.outputs.get(output_id) or {}).get("volume")
        if volume is None:
            return None
        vmin = float(volume.get("min", 0))
        vmax = float(volume.get("max", 100))
        step = float(volume.get("step", 1))
        factor = (vmax - vmin) / 100 if vmax != vmin else 1.0
        current_raw = float(volume.get("value", vmin))
        return vmin, vmax, step, factor, current_raw

    def _set_result(self, output_id: str, requested_percent: int) -> VolumeChangeResult:
        meta = self._volume_metadata(output_id)
        if meta is None:
            return VolumeChangeResult(requested_percent, requested_percent)
        vmin, _vmax, step, factor, current_raw = meta
        if factor <= 0:
            return VolumeChangeResult(requested_percent, requested_percent)
        previous = int(round((current_raw - vmin) / factor))
        raw = vmin + requested_percent * factor
        if step == int(step):
            raw = int(round(raw))
        achieved = int(round((raw - vmin) / factor))
        return VolumeChangeResult(previous, achieved)

    def _change_result(self, output_id: str, delta_percent: int) -> VolumeChangeResult:
        meta = self._volume_metadata(output_id)
        if meta is None:
            return VolumeChangeResult(0, 0)
        vmin, vmax, step, factor, current_raw = meta
        if factor <= 0:
            return VolumeChangeResult(0, 0)
        previous = int(round((current_raw - vmin) / factor))
        raw_delta = delta_percent * factor
        if step == int(step):
            raw_delta = int(round(raw_delta))
        new_raw = max(vmin, min(vmax, current_raw + raw_delta))
        achieved = int(round((new_raw - vmin) / factor))
        return VolumeChangeResult(previous, achieved)

    def get_volume_percent(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> Optional[int]:
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self._raise_if_fixed_volume(output_id)
        return self.api.get_volume_percent(output_id=output_id)

    def set_volume_percent(
        self,
        volume: int,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> VolumeChangeResult:
        """Set the output volume by percent (0-100). Returns the
        previous and achieved percents (achieved may differ from
        ``volume`` when the device quantises to a coarser step)."""
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self._raise_if_fixed_volume(output_id)
        result = self._set_result(output_id, volume)
        self.api.set_volume_percent(output_id=output_id, absolute_value=volume)
        return result

    def set_volume_absolute(
        self,
        volume: int,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        """Set the output volume to a raw value in the device's native
        scale (between the device's reported ``min`` and ``max``)."""
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self._raise_if_fixed_volume(output_id)
        self.api.change_volume_raw(output_id, volume, "absolute")

    def change_volume_percent(
        self,
        delta: int,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> VolumeChangeResult:
        """Change the output volume by a relative percent. Returns the
        previous and achieved percents."""
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self._raise_if_fixed_volume(output_id)
        result = self._change_result(output_id, delta)
        self.api.change_volume_percent(output_id=output_id, relative_value=delta)
        return result

    def mute(
        self,
        mute: bool,
        zone: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self.api.mute(output_id=output_id, mute=mute)

    def play_from_here(self, queue_item_id: int, zone: Optional[str] = None) -> None:
        # Validate against stored queue data
        queue_items = self.get_queue_items(zone=zone)
        valid_ids = {item.get("queue_item_id") for item in queue_items}
        if valid_ids and queue_item_id not in valid_ids:
            sample_refs = [f"{qid:05x}" for qid in sorted(valid_ids)[:5]]
            raise ValueError(
                f"Invalid queue_item_id {queue_item_id}. "
                f"Convert the 5-char hex reference to an integer "
                f"(e.g. {sample_refs[0]} = {int(sample_refs[0], 16)}). "
                f"This is NOT the track position number."
            )
        output_id = self._lookup_output_id(zone)
        self.api._request(
            "com.roonlabs.transport:2/play_from_here",
            {"zone_or_output_id": output_id, "queue_item_id": queue_item_id},
        )

    def set_auto_radio(self, auto_radio: bool, zone: Optional[str] = None) -> None:
        output_id = self._lookup_output_id(zone)
        self.api._request(
            "com.roonlabs.transport:2/change_settings",
            {"zone_or_output_id": output_id, "auto_radio": auto_radio},
        )

    def pause_all(self) -> None:
        self.api.pause_all()

    def mute_all(self) -> None:
        self.api._request("com.roonlabs.transport:2/mute_all", {"how": "mute"})

    def unmute_all(self) -> None:
        self.api._request("com.roonlabs.transport:2/mute_all", {"how": "unmute"})

    def standby(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
        control_key: Optional[str] = None,
    ) -> None:
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self.api.standby(output_id=output_id, control_key=control_key)

    def convenience_switch(
        self,
        zone: Optional[str] = None,
        output: Optional[str] = None,
        control_key: Optional[str] = None,
    ) -> None:
        output_id = self._lookup_output_id_for_controls(zone=zone, output=output)
        self.api.convenience_switch(output_id=output_id, control_key=control_key)

    def group_zones(self, zones: List[str]) -> None:
        if len(zones) < 2:
            raise ValueError("At least two zones are required to create a group")
        output_ids = [self._resolve_output_id(name) for name in zones]
        self.api.group_outputs(output_ids=output_ids)

    def ungroup_zones(self, zones: List[str]) -> None:
        if not zones:
            raise ValueError("At least one zone is required to ungroup outputs")
        output_ids = [self._resolve_output_id(name) for name in zones]
        self.api.ungroup_outputs(output_ids=output_ids)

    def _resolve_output_id(self, name: str) -> str:
        """Resolve a name to an output_id.

        Tries output display names first (stable across grouping state),
        then falls back to zone display names for ungrouped zones where
        the output and zone share the same name.
        """
        # Try output display names first
        for output_item in self.api.outputs.values():
            if output_item["display_name"].lower() == name.lower():
                return output_item["output_id"]
        # Fall back to zone display name → first output
        for zone_item in self.api.zones.values():
            if zone_item["display_name"].lower() == name.lower():
                outputs = zone_item.get("outputs", [])
                if outputs:
                    return outputs[0]["output_id"]
        raise ZoneLookupError(f"Unknown zone or output: {name}")
