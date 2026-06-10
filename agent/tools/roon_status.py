import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from app.exceptions import UnsupportedActionError
from app.roon.compact_formatting import _compact_items
from app.roon.tag_expansion import _format_display_item, transform_queue_items_to_result_shape
from app.roon.zone_formatting import build_compact_playback_status

RoonStatusOperations = Literal[
    "get_zones_status",
    "get_queue_status",
]


class RoonStatusToolInputSchema(BaseModel):
    """Input schema for reading zone or queue status."""

    operation: RoonStatusOperations = Field(
        ...,
        description="Status operation to perform",
    )
    zone: Optional[Union[str, List[str]]] = Field(
        None,
        description="Target zone(s). Omit for all zones; pass a zone name or list of names for specific zones.",
    )


class RoonStatusToolOutputSchema(BaseModel):
    """Output schema containing Roon status."""

    operation: str = Field(..., description="Status operation executed")
    zone: Optional[str] = Field(None, description="Zone this status pertains to")
    result: str = Field(..., description="Status result")
    error: Optional[str] = Field(None, description="Error message if retrieval failed")


class RoonStatusToolConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    roon_connection: Optional[Any] = None
    # resolve_zone is required (see RoonActionToolConfig for rationale).
    resolve_zone: Any = ...
    queue_display_cache: Optional[Any] = None
    format_zone_label: Optional[Any] = None
    get_last_played_dict: Optional[Any] = None
    # Callables that surface fresh alias / default-zone info to the
    # formatter — these change over time, hence callables not snapshots.
    get_reverse_aliases: Optional[Any] = None
    get_default_zone: Optional[Any] = None


class RoonStatusTool:
    """
    Tool for reading zone and queue status from Roon.
    """

    input_schema = RoonStatusToolInputSchema
    output_schema = RoonStatusToolOutputSchema
    parallel_safe = True

    def __init__(self, config: RoonStatusToolConfig) -> None:
        self.config = config
        self.roon_connection = config.roon_connection
        self._resolve_zone = config.resolve_zone
        self._queue_display_cache = config.queue_display_cache
        self._format_zone_label = config.format_zone_label
        self._get_last_played_dict = config.get_last_played_dict
        self._get_reverse_aliases = config.get_reverse_aliases
        self._get_default_zone = config.get_default_zone

    @staticmethod
    def _restructure_queue_trace(result_text: str) -> Dict[str, Any]:
        """Parse flat queue result text into per-zone structured arrays.

        Input format (from _queue_status):
            Queue for Zone A (N tracks)

            (1) [ref] Title | Album | Artist
            (2) [ref] Title | Album | Artist

            Queue for Zone B (M tracks)

            (1) [ref] Title | Album | Artist

            No queue data: Zone C, Zone D

        Output:
            {"queues": [{"zone": "Zone A", "items": [...]}, ...],
             "no_queue_zones": ["Zone C", "Zone D"]}
        """
        queues: List[Dict[str, Any]] = []
        no_queue_zones: List[str] = []

        header_re = re.compile(r"^Queue for (.+?) \(\d+ tracks?\)$", re.MULTILINE)
        headers = list(header_re.finditer(result_text))

        for i, match in enumerate(headers):
            zone_name = match.group(1)
            start = match.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(result_text)
            section = result_text[start:end]

            items = [
                line.strip()
                for line in section.split("\n")
                if line.strip().startswith("(")
            ]
            queues.append({"zone": zone_name, "items": items})

        no_data_match = re.search(r"No queue data:\s*(.+)$", result_text)
        if no_data_match:
            no_queue_zones = [z.strip() for z in no_data_match.group(1).split(",")]

        return {"queues": queues, "no_queue_zones": no_queue_zones}

    def compact_trace(self, output: "RoonStatusToolOutputSchema") -> Optional[Dict[str, Any]]:
        """Restructure queue status for trace; None for default compaction."""
        raw = output.model_dump(mode="json")
        if raw.get("operation") == "get_queue_status":
            return self._restructure_queue_trace(raw.get("result", ""))
        return None

    def _resolve(self, zone: Optional[str]) -> Optional[str]:
        """Resolve a zone name or alias to a real Roon zone display name."""
        if zone and self._resolve_zone:
            return self._resolve_zone(zone)
        return zone

    def _zones_status(self, zones: Optional[List[str]]) -> RoonStatusToolOutputSchema:
        if zones:
            snapshots = [self.roon_connection.get_zone_snapshot(zone=z) for z in zones]
        else:
            snapshots = [
                z for z in self.roon_connection.get_zones_snapshot()
                if z.get("outputs")
            ]
        zone_label = ", ".join(zones) if zones else None
        last_played_map = self._build_last_played_map(snapshots)
        aliases = self._get_reverse_aliases() if self._get_reverse_aliases else {}
        default_zone = self._get_default_zone() if self._get_default_zone else None
        return RoonStatusToolOutputSchema(
            operation="get_zones_status",
            zone=zone_label,
            result=build_compact_playback_status(
                snapshots, aliases, default_zone,
                last_played_by_zone_id=last_played_map,
            ),
        )

    def _build_last_played_map(self, zones: List[dict]) -> Optional[dict]:
        if self._get_last_played_dict is None:
            return None
        result: dict = {}
        for zone in zones:
            zone_id = zone.get("zone_id")
            if not zone_id:
                continue
            entry = self._get_last_played_dict(zone_id)
            if entry:
                result[zone_id] = entry
        return result

    def _fetch_single_queue(self, zone_name: str) -> Optional[str]:
        """Fetch and cache a single zone's queue. Returns compact text or None."""
        raw_items = self.roon_connection.get_queue_items(zone=zone_name)
        if not raw_items:
            return None

        ref_map = self.roon_connection.get_queue_references(zone=zone_name)
        transformed = transform_queue_items_to_result_shape(raw_items, ref_map=ref_map)
        compact_lines = _compact_items(transformed)
        compact_text = "\n".join(compact_lines)

        count = len(raw_items)
        count_label = f"{count} track" if count == 1 else f"{count} tracks"
        label = self._format_zone_label(zone_name) if self._format_zone_label else zone_name
        summary = f"Queue for {label} ({count_label})"

        # Cache a display-formatted block for <queue zone="..."/> expansion.
        # Uses clean numbered lines (1. Title — Album — Artist) matching
        # album listing format, not the compact trace format with hex refs.
        if self._queue_display_cache is not None:
            display_lines = [
                _format_display_item(item, idx)
                for idx, item in enumerate(transformed, 1)
            ]
            self._queue_display_cache[zone_name] = (
                f"<list><summary>{summary}</summary>\n\n"
                f"{'\n'.join(display_lines)}\n</list>"
            )

        return f"{summary}\n\n{compact_text}"

    def _queue_status(self, zones: Optional[List[str]]) -> RoonStatusToolOutputSchema:
        if zones is None:
            # All zones
            zones_to_fetch = [
                z.get("display_name", "")
                for z in self.roon_connection.get_zones_snapshot()
                if z.get("outputs") and z.get("display_name")
            ]
        else:
            zones_to_fetch = zones

        queue_sections = []
        no_queue_zones = []
        for zone_name in zones_to_fetch:
            result_text = self._fetch_single_queue(zone_name)
            if result_text:
                queue_sections.append(result_text)
            else:
                no_queue_zones.append(zone_name)

        zone_label = ", ".join(zones) if zones else None

        if not queue_sections:
            return RoonStatusToolOutputSchema(
                operation="get_queue_status",
                zone=zone_label,
                result=f"No queue data available for {zone_label or 'any zone'}",
            )

        combined = "\n\n".join(queue_sections)
        if no_queue_zones:
            combined += f"\n\nNo queue data: {', '.join(no_queue_zones)}"

        return RoonStatusToolOutputSchema(
            operation="get_queue_status",
            zone=zone_label,
            result=combined,
        )

    def _normalise_zones(self, zone_input: Optional[Union[str, List[str]]]) -> Optional[List[str]]:
        """Normalise zone input to a resolved list, or None for all."""
        if zone_input is None:
            return None
        raw = [zone_input] if isinstance(zone_input, str) else list(zone_input)
        return [self._resolve(z) for z in raw if z]

    async def run_async(self, params: RoonStatusToolInputSchema) -> RoonStatusToolOutputSchema:
        try:
            zones = self._normalise_zones(params.zone)
        except Exception as exc:
            return RoonStatusToolOutputSchema(
                operation=params.operation,
                zone=params.zone if isinstance(params.zone, str) else str(params.zone),
                result=f"Zone resolution failed: {exc}",
                error=str(exc),
            )
        try:
            if params.operation == "get_zones_status":
                return self._zones_status(zones=zones)
            if params.operation == "get_queue_status":
                return self._queue_status(zones=zones)
            raise UnsupportedActionError(f"Unknown status operation '{params.operation}'")
        except Exception as exc:
            return RoonStatusToolOutputSchema(
                operation=params.operation,
                zone=str(params.zone),
                result="Status retrieval failed",
                error=str(exc),
            )

    def run(self, params: RoonStatusToolInputSchema) -> RoonStatusToolOutputSchema:
        with ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, self.run_async(params)).result()
