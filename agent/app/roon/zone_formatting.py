"""Compact, LLM-friendly zone-status formatting.

Renders Roon zone state into a sectioned text block:

    CURRENT STATUS: → LAST PLAYED:

The default zone is marked inline within CURRENT STATUS with a
``[DEFAULT ZONE]`` annotation on the matching zone's identifier line.
The structure exists to make CURRENT STATUS (live state) and
LAST PLAYED (historical) impossible to confuse.

Two entry points share the same format:

- ``build_compact_zone_status`` — context provider; no seek position.
- ``build_compact_playback_status`` — ``roon_status`` tool output;
  same format with seek added to CURRENT STATUS entries.

Test contract lives in ``tests/test_compact_zone_status.py``.
"""

from __future__ import annotations

from typing import Optional

from app.time_utils import format_relative_time

# ── Cell formatters ─────────────────────────────────────────────────


def _format_track_length(seconds: int | float | None) -> str:
    """Format a track length in seconds as m:ss; empty string if absent."""
    if not seconds:
        return ""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _format_output_volume(output: dict) -> str:
    """Format a single output's volume, normalising absent blocks."""
    vol = output.get("volume")
    if not vol:
        return "not controllable (fixed)"
    value = vol.get("value")
    parts = [f"{value}%"] if value is not None else ["unknown"]
    if vol.get("is_muted"):
        parts.append("(muted)")
    return " ".join(parts)


def _format_relative_time(seconds_ago: float | None) -> str:
    """Thin wrapper around the shared helper for the zone-status case
    where None must render as an empty string (no last-played history)."""
    if seconds_ago is None:
        return ""
    return format_relative_time(float(seconds_ago))


def _format_track_identifier(line1: str, line2: str, line3: str) -> str:
    """Build the quoted 3-part track identifier.

    Quotes wrap the entire em-dash-joined block so the LLM can find the
    boundary regardless of em-dashes or parens in the title / artist /
    album fields.
    """
    return f'"{line1} — {line2} — {line3}"'


def _state_marker(state: str) -> str:
    """Return the bracketed state marker for a non-playing state.

    Playing state renders nothing (live track is the implicit marker).
    Paused, stopped, and unknown each get their own bracketed token.
    """
    if state == "playing":
        return ""
    return f"[{state.upper()}]"


def _group_annotation(outputs: list[dict], aliases: dict[str, str]) -> str:
    """Inline ``[GROUPED: ...]`` annotation for grouped zones; empty
    string for ungrouped zones. Each member is shown by display name
    with its alias if it has one — helpful for the LLM correlating
    per-output volumes and resolving user queries by friendly name.
    """
    if len(outputs) <= 1:
        return ""
    parts = []
    for output in outputs:
        name = output.get("display_name", "?")
        alias = aliases.get(name)
        parts.append(f"{name} (alias: {alias})" if alias else name)
    return f"[GROUPED: {', '.join(parts)}]"


def _zone_identifier(
    zone: dict,
    aliases: dict[str, str],
    include_group_annotation: bool,
) -> str:
    """Compose a zone's identifier line: display name, optional alias
    (ungrouped zones only), and optional inline group annotation."""
    display_name = zone.get("display_name", "Unknown")
    outputs = zone.get("outputs", []) or []
    is_grouped = len(outputs) > 1
    alias = aliases.get(display_name)

    parts = [display_name]
    # Group entities don't have aliases of their own (only individual
    # outputs do); only show alias for ungrouped zones.
    if alias and not is_grouped:
        parts.append(f"(alias: {alias})")
    line = " ".join(parts)

    if include_group_annotation and is_grouped:
        line += " " + _group_annotation(outputs, aliases)
    return line


def _volume_line(outputs: list[dict]) -> str:
    """Render the ``Volume: ...`` line for a zone."""
    if len(outputs) == 1:
        return f"Volume: {_format_output_volume(outputs[0])}"
    vol_parts = [
        f"{o.get('display_name', '?')}: {_format_output_volume(o)}"
        for o in outputs
    ]
    return f"Volume: {' | '.join(vol_parts)}"


# ── Section renderers ───────────────────────────────────────────────


def _render_current_status_section(
    zones: list[dict],
    aliases: dict[str, str],
    include_seek: bool,
    default_zone: Optional[str],
) -> list[str]:
    """Per-zone live state — what each zone is doing right now. The
    zone whose display name matches ``default_zone`` gets a
    ``[DEFAULT ZONE]`` annotation appended to its identifier line."""
    lines: list[str] = ["CURRENT STATUS:"]
    for zone in zones:
        lines.append("")
        identifier = _zone_identifier(zone, aliases, include_group_annotation=True)
        if default_zone and zone.get("display_name") == default_zone:
            identifier += " [DEFAULT ZONE]"
        lines.append(f"  {identifier}")
        lines.extend(_render_now_playing_block(zone, include_seek))
        outputs = zone.get("outputs", []) or []
        lines.append(f"    {_volume_line(outputs)}")
    lines.append("")
    return lines


def _render_now_playing_block(zone: dict, include_seek: bool) -> list[str]:
    """The ``Now playing:`` line plus optional Shuffle/Repeat and
    Position lines. Always emits exactly one ``Now playing:`` line so
    the LLM can scan zones uniformly."""
    state = (zone.get("state") or "unknown").lower()
    lines: list[str] = []

    if state in {"playing", "paused"}:
        now_playing = zone.get("now_playing") or {}
        three_line = now_playing.get("three_line") or {}
        line1 = three_line.get("line1") or "Unknown"
        line2 = three_line.get("line2") or "Unknown"
        line3 = three_line.get("line3") or "Unknown"
        track = _format_track_identifier(line1, line2, line3)
        length_str = _format_track_length(now_playing.get("length"))
        suffix = f" ({length_str})" if length_str else ""
        marker = _state_marker(state)
        marker_suffix = f" {marker}" if marker else ""
        lines.append(f"    Now playing: {track}{suffix}{marker_suffix}")

        if include_seek:
            seek = zone.get("seek_position")
            track_length = now_playing.get("length")
            seek_str = _format_track_length(seek) if seek is not None else "0:00"
            length_full = _format_track_length(track_length) if track_length else "?"
            lines.append(f"    Position: {seek_str} / {length_full}")

        settings = zone.get("settings") or {}
        shuffle = "on" if settings.get("shuffle") else "off"
        repeat_raw = settings.get("loop") or "disabled"
        repeat = "off" if repeat_raw == "disabled" else repeat_raw
        lines.append(f"    Shuffle: {shuffle} | Repeat: {repeat}")
    else:
        marker = _state_marker(state)
        lines.append(f"    Now playing: Nothing {marker}")

    return lines


def _render_last_played_section(
    zones: list[dict],
    aliases: dict[str, str],
    last_played_by_zone_id: Optional[dict[str, dict]],
) -> list[str]:
    """LAST PLAYED entries, one per zone-with-history. Omits entirely
    when no zone has any history (header included)."""
    if not last_played_by_zone_id:
        return []

    entries: list[tuple[dict, dict]] = []
    for zone in zones:
        zone_id = zone.get("zone_id")
        if not zone_id:
            continue
        last_played = last_played_by_zone_id.get(zone_id)
        if not last_played or not last_played.get("title"):
            continue
        entries.append((zone, last_played))

    if not entries:
        return []

    lines: list[str] = ["LAST PLAYED:", ""]
    for zone, last_played in entries:
        identifier = _zone_identifier(zone, aliases, include_group_annotation=False)
        title = last_played.get("title") or ""
        artist = last_played.get("artist") or "Unknown"
        album = last_played.get("album") or "Unknown"
        track = _format_track_identifier(title, artist, album)
        relative = _format_relative_time(last_played.get("seconds_ago"))
        suffix = f" ({relative})" if relative else ""
        lines.append(f"  {identifier}: {track}{suffix}")
    lines.append("")
    return lines


# ── Public API ──────────────────────────────────────────────────────


_PREAMBLE = (
    "Zones with current status (this is the live Roon state at this "
    "instant — default zone, current zone state, and aliases shown "
    "here are authoritative; any zone references in the Execution "
    "Trace or Conversation History below may be stale. Seek position "
    "excluded — use roon_status get_zones_status if needed.)"
)


def _has_renderable_zones(zones: list[dict]) -> list[dict]:
    """Filter out ghost zones (no outputs) — they appear in Roon's
    snapshot during transient states but have nothing to render."""
    return [z for z in zones if z.get("outputs")]


def build_compact_zone_status(
    zones: list[dict],
    zone_aliases: dict[str, str],
    default_zone: Optional[str],
    last_played_by_zone_id: Optional[dict[str, dict]] = None,
) -> str:
    """LLM-friendly zone-status block for the system-prompt context
    provider. Seek position excluded — the model can fetch it via
    ``roon_status get_zones_status`` when needed."""
    active = _has_renderable_zones(zones)
    if not active:
        return "No zones available."

    reverse_aliases = _normalise_reverse_aliases(zone_aliases)
    lines: list[str] = [_PREAMBLE, ""]
    lines.extend(_render_current_status_section(
        active, reverse_aliases, include_seek=False, default_zone=default_zone,
    ))
    lines.extend(_render_last_played_section(active, reverse_aliases, last_played_by_zone_id))
    return "\n".join(lines).rstrip()


def build_compact_playback_status(
    zones: list[dict],
    zone_aliases: dict[str, str],
    default_zone: Optional[str],
    last_played_by_zone_id: Optional[dict[str, dict]] = None,
) -> str:
    """Tool-output variant for ``roon_status get_zones_status``. Same
    sectioned format as the context provider, with seek positions added
    to CURRENT STATUS entries."""
    if not zones:
        return "No zones available."

    reverse_aliases = _normalise_reverse_aliases(zone_aliases)
    lines: list[str] = []
    lines.extend(_render_current_status_section(
        zones, reverse_aliases, include_seek=True, default_zone=default_zone,
    ))
    lines.extend(_render_last_played_section(zones, reverse_aliases, last_played_by_zone_id))
    return "\n".join(lines).rstrip()


def _normalise_reverse_aliases(aliases: dict[str, str]) -> dict[str, str]:
    """The aliases map arrives keyed by display_name → alias.
    Return it as-is; the helper exists so callers can pass either
    direction without an unexpected KeyError."""
    return aliases or {}
