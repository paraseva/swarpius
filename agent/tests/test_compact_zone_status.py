"""Tests for compact zone status formatting — context provider and tool output.

The output is composed of two sections in canonical order:

    CURRENT STATUS: → LAST PLAYED:

The default zone is marked inline within CURRENT STATUS via a
``[DEFAULT ZONE]`` annotation on the relevant zone's identifier line —
no separate header section. The structure exists to make ``Now playing``
(live state) and ``LAST PLAYED`` (historical) impossible to confuse.

Test classes:

- ``TestSectionedStructure`` pins the structural contract (sections in
  order, absent sections collapse cleanly, group/alias annotations
  appear in the right places).
- ``TestNowPlayingFormatting``, ``TestLastPlayedInjection``,
  ``TestMalformedZonePayloads``, ``TestCompactPlaybackStatus`` exercise
  the per-cell rendering details and edge cases.
"""

from app.roon.zone_formatting import (
    build_compact_playback_status,
    build_compact_zone_status,
)


def _make_output(display_name, volume_value=None, volume_type="number", is_muted=False):
    """Build a minimal Roon output dict with optional volume."""
    output = {
        "output_id": f"id_{display_name.lower().replace(' ', '_')}",
        "display_name": display_name,
    }
    if volume_type is not None:
        output["volume"] = {
            "type": volume_type,
            "value": volume_value,
            "is_muted": is_muted,
            "min": 0,
            "max": 100,
            "step": 1,
        }
    return output


def _make_zone(
    display_name,
    state="playing",
    outputs=None,
    now_playing_lines=None,
    track_length=None,
    shuffle=False,
    loop="disabled",
    seek_position=42,
):
    """Build a minimal Roon zone dict."""
    if outputs is None:
        outputs = [_make_output(display_name, volume_value=50)]
    three_line = {}
    if now_playing_lines:
        three_line = {
            "line1": now_playing_lines[0],
            "line2": now_playing_lines[1] if len(now_playing_lines) > 1 else None,
            "line3": now_playing_lines[2] if len(now_playing_lines) > 2 else None,
        }
    return {
        "zone_id": f"zone_{display_name.lower().replace(' ', '_')}",
        "display_name": display_name,
        "state": state,
        "outputs": outputs,
        "seek_position": seek_position,
        "settings": {"shuffle": shuffle, "loop": loop},
        "now_playing": {
            "three_line": three_line,
            "length": track_length,
        } if now_playing_lines else {},
    }


def _index_of(haystack: str, needle: str) -> int:
    """Helper: assert needle present and return its index."""
    idx = haystack.find(needle)
    assert idx >= 0, f"expected to find {needle!r} in:\n{haystack}"
    return idx


class TestSectionedStructure:
    """Three-section output, canonical order, no concerns interleaved."""

    def test_sections_appear_in_canonical_order(self):
        zones = [
            _make_zone(
                "Speakers",
                state="playing",
                now_playing_lines=["Song", "Artist", "Album"],
                track_length=180,
            ),
        ]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "Older", "artist": "X", "album": "Y", "seconds_ago": 60,
            },
        }
        result = build_compact_zone_status(
            zones, {}, "Speakers", last_played_by_zone_id=last_played,
        )
        current_idx = _index_of(result, "CURRENT STATUS:")
        last_idx = _index_of(result, "LAST PLAYED:")
        assert current_idx < last_idx, f"section order wrong:\n{result}"

    def test_no_separate_default_zone_section(self):
        """The standalone ``DEFAULT ZONE:`` header section has been
        removed — default zone is marked inline within CURRENT STATUS."""
        zones = [_make_zone("Speakers", state="stopped")]
        result = build_compact_zone_status(zones, {}, "Speakers")
        assert "DEFAULT ZONE:" not in result

    def test_default_zone_marker_appears_on_matching_current_status_entry(self):
        """The default zone gets a ``[DEFAULT ZONE]`` annotation on its
        CURRENT STATUS identifier line (and only on that zone's line)."""
        zones = [
            _make_zone("Speakers", state="stopped"),
            _make_zone("RME", state="stopped"),
        ]
        result = build_compact_zone_status(zones, {}, "Speakers")
        # Find the Speakers line in CURRENT STATUS and confirm the marker.
        speakers_line = next(
            line for line in result.splitlines() if line.startswith("  Speakers")
        )
        rme_line = next(
            line for line in result.splitlines() if line.startswith("  RME")
        )
        assert "[DEFAULT ZONE]" in speakers_line
        assert "[DEFAULT ZONE]" not in rme_line

    def test_default_zone_marker_resolves_via_alias(self):
        """The default zone in Roon is the display name (e.g. 'MDAC+ USB');
        the user-facing alias ('Speakers') should still appear via the
        existing identifier rendering, and the marker should sit alongside
        it on the same line."""
        zones = [_make_zone("MDAC+ USB", state="stopped")]
        result = build_compact_zone_status(
            zones, {"MDAC+ USB": "Speakers"}, "MDAC+ USB",
        )
        line = next(
            ln for ln in result.splitlines() if ln.startswith("  MDAC+ USB")
        )
        assert "(alias: Speakers)" in line
        assert "[DEFAULT ZONE]" in line

    def test_no_default_zone_marker_when_default_unset(self):
        """When no default zone is configured, no CURRENT STATUS entry
        carries the ``[DEFAULT ZONE]`` marker."""
        zones = [
            _make_zone("Speakers", state="stopped"),
            _make_zone("RME", state="stopped"),
        ]
        result = build_compact_zone_status(zones, {}, None)
        assert "[DEFAULT ZONE]" not in result

    def test_preamble_names_execution_trace_and_conversation_history(self):
        """The CURRENT STATUS preamble must name *both* competing
        stale-state sources by name. Both share the same
        authoritative-vs-stale framing."""
        zones = [_make_zone("Speakers", state="stopped")]
        result = build_compact_zone_status(zones, {}, "Speakers")
        assert "Execution Trace" in result
        assert "Conversation History" in result

    def test_current_status_always_present_when_zones_exist(self):
        zones = [_make_zone("Speakers", state="stopped")]
        result = build_compact_zone_status(zones, {}, None)
        assert "CURRENT STATUS:" in result

    def test_last_played_section_omitted_when_no_history(self):
        zones = [_make_zone("Speakers", state="stopped")]
        result = build_compact_zone_status(zones, {}, None, last_played_by_zone_id={})
        # Whole section header absent — not an empty section.
        assert "LAST PLAYED:" not in result

    def test_last_played_section_present_when_any_zone_has_history(self):
        zones = [
            _make_zone("Speakers", state="stopped"),
            _make_zone("RME", state="stopped"),
        ]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "T", "artist": "A", "album": "B", "seconds_ago": 60,
            },
        }
        result = build_compact_zone_status(
            zones, {}, None, last_played_by_zone_id=last_played,
        )
        assert "LAST PLAYED:" in result

    def test_last_played_section_has_no_explanatory_preamble(self):
        """The section is unambiguous on its own — entries are just
        the last track played on each zone, distinct from current
        state. No preamble text needed; the tokens belong elsewhere."""
        zones = [_make_zone("Speakers", state="stopped")]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "T", "artist": "A", "album": "B", "seconds_ago": 60,
            },
        }
        result = build_compact_zone_status(
            zones, {}, None, last_played_by_zone_id=last_played,
        )
        # Header is present; the descriptive sentence is not.
        assert "LAST PLAYED:" in result
        assert "most recent finished track" not in result
        assert "track immediately before the current one" not in result


class TestNowPlayingFormatting:
    """`Now playing:` is the line under every CURRENT STATUS entry —
    same prefix for playing / paused / stopped zones so the LLM sees a
    uniform scannable pattern. State markers ride at the end of the
    line for paused / stopped; playing has no marker."""

    def test_playing_zone_has_quoted_track_identifier(self):
        zones = [_make_zone(
            "Speakers",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=180,
        )]
        result = build_compact_zone_status(zones, {}, None)
        # Quoted three-part identifier, then length in parens.
        assert 'Now playing: "Song — Artist — Album" (3:00)' in result

    def test_stopped_zone_shows_now_playing_nothing_stopped(self):
        zones = [_make_zone("Speakers", state="stopped")]
        result = build_compact_zone_status(zones, {}, None)
        # The same "Now playing:" prefix, with explicit Nothing + [STOPPED].
        assert "Now playing: Nothing [STOPPED]" in result

    def test_paused_zone_keeps_track_and_appends_paused_marker(self):
        zones = [_make_zone(
            "Speakers",
            state="paused",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=180,
        )]
        result = build_compact_zone_status(zones, {}, None)
        assert 'Now playing: "Song — Artist — Album" (3:00) [PAUSED]' in result

    def test_track_with_em_dash_in_title_is_disambiguated_by_quotes(self):
        zones = [_make_zone(
            "Speakers",
            state="playing",
            now_playing_lines=["Sonny — Cher Live", "Artist", "Album"],
            track_length=180,
        )]
        result = build_compact_zone_status(zones, {}, None)
        # The whole 3-part identifier is wrapped in quotes — the LLM
        # can find the boundary regardless of em-dashes in the content.
        assert '"Sonny — Cher Live — Artist — Album"' in result

    def test_track_with_parens_in_title_is_disambiguated_by_quotes(self):
        zones = [_make_zone(
            "Speakers",
            state="playing",
            now_playing_lines=[
                "747 (Strangers in the Night)",
                "Saxon",
                "Wheels of Steel (Remastered 2009)",
            ],
            track_length=342,
        )]
        result = build_compact_zone_status(zones, {}, None)
        # Quotes wrap the identifier so the trailing length (5:42) is
        # unambiguously the length, not part of the album name.
        assert (
            'Now playing: "747 (Strangers in the Night) — Saxon — '
            'Wheels of Steel (Remastered 2009)" (5:42)' in result
        )


class TestGroupAndAliasAnnotations:
    """Group annotation appears inline in CURRENT STATUS only.
    Aliases for ungrouped zones appear in CURRENT STATUS AND LAST
    PLAYED (helps the LLM resolve user queries by friendly name).
    Group entities have no alias of their own and render as bare
    display names in DEFAULT ZONE and LAST PLAYED."""

    def test_group_annotation_inline_in_current_status(self):
        zones = [_make_zone(
            "BT-W5 Akash + 1",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=180,
            outputs=[
                _make_output("BT-W5 Akash", volume_value=30),
                _make_output("Chord Qutest", volume_type=None),
            ],
        )]
        aliases = {"BT-W5 Akash": "Headphones", "Chord Qutest": "Qutest"}
        result = build_compact_zone_status(zones, aliases, None)
        current_block = result.split("LAST PLAYED:")[0] if "LAST PLAYED:" in result else result
        assert (
            "[GROUPED: BT-W5 Akash (alias: Headphones), "
            "Chord Qutest (alias: Qutest)]" in current_block
        )

    def test_group_annotation_absent_from_default_zone_section(self):
        zones = [_make_zone(
            "BT-W5 Akash + 1",
            state="stopped",
            outputs=[
                _make_output("BT-W5 Akash", volume_value=30),
                _make_output("Chord Qutest", volume_type=None),
            ],
        )]
        aliases = {"BT-W5 Akash": "Headphones", "Chord Qutest": "Qutest"}
        result = build_compact_zone_status(zones, aliases, "BT-W5 Akash + 1")
        default_block = result.split("CURRENT STATUS:")[0]
        assert "GROUPED" not in default_block

    def test_group_annotation_absent_from_last_played_section(self):
        zones = [_make_zone(
            "BT-W5 Akash + 1",
            state="stopped",
            outputs=[
                _make_output("BT-W5 Akash", volume_value=30),
                _make_output("Chord Qutest", volume_type=None),
            ],
        )]
        aliases = {"BT-W5 Akash": "Headphones", "Chord Qutest": "Qutest"}
        last_played = {
            zones[0]["zone_id"]: {
                "title": "T", "artist": "A", "album": "B", "seconds_ago": 60,
            },
        }
        result = build_compact_zone_status(
            zones, aliases, None, last_played_by_zone_id=last_played,
        )
        last_block = result.split("LAST PLAYED:")[1]
        assert "GROUPED" not in last_block

    def test_alias_shown_in_current_status_for_ungrouped_zone(self):
        zones = [_make_zone("MDAC+ USB", state="stopped")]
        aliases = {"MDAC+ USB": "Speakers"}
        result = build_compact_zone_status(zones, aliases, None)
        current_block = result.split("LAST PLAYED:")[0] if "LAST PLAYED:" in result else result
        assert "MDAC+ USB (alias: Speakers)" in current_block

    def test_alias_shown_in_last_played_for_ungrouped_zone(self):
        zones = [_make_zone("MDAC+ USB", state="stopped")]
        aliases = {"MDAC+ USB": "Speakers"}
        last_played = {
            zones[0]["zone_id"]: {
                "title": "T", "artist": "A", "album": "B", "seconds_ago": 60,
            },
        }
        result = build_compact_zone_status(
            zones, aliases, None, last_played_by_zone_id=last_played,
        )
        last_block = result.split("LAST PLAYED:")[1]
        # Direct scan target for user queries like "what was Speakers playing?"
        assert "MDAC+ USB (alias: Speakers):" in last_block

    def test_grouped_zone_uses_bare_display_name_in_last_played(self):
        zones = [_make_zone(
            "BT-W5 Akash + 1",
            state="stopped",
            outputs=[
                _make_output("BT-W5 Akash", volume_value=30),
                _make_output("Chord Qutest", volume_type=None),
            ],
        )]
        aliases = {"BT-W5 Akash": "Headphones", "Chord Qutest": "Qutest"}
        last_played = {
            zones[0]["zone_id"]: {
                "title": "T", "artist": "A", "album": "B", "seconds_ago": 60,
            },
        }
        result = build_compact_zone_status(
            zones, aliases, None, last_played_by_zone_id=last_played,
        )
        last_block = result.split("LAST PLAYED:")[1]
        assert "BT-W5 Akash + 1:" in last_block
        assert "alias:" not in last_block


class TestLastPlayedInjection:
    """``last_played_by_zone_id`` injects historical-track info into
    the LAST PLAYED section. Each entry has a quoted three-part
    identifier and a time marker (or 'just now')."""

    def test_last_played_entry_has_quoted_track_and_time(self):
        zones = [_make_zone("Speakers", state="stopped")]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "Song", "artist": "Artist", "album": "Album",
                "seconds_ago": 90,
            },
        }
        result = build_compact_zone_status(
            zones, {}, None, last_played_by_zone_id=last_played,
        )
        assert '"Song — Artist — Album" (1 min ago)' in result

    def test_last_played_omits_zones_without_history(self):
        zones = [
            _make_zone("Speakers", state="stopped"),
            _make_zone("RME", state="stopped"),
        ]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "Only One", "artist": "A", "album": "B",
                "seconds_ago": 30,
            },
        }
        result = build_compact_zone_status(
            zones, {}, None, last_played_by_zone_id=last_played,
        )
        last_block = result.split("LAST PLAYED:")[1]
        assert "Only One" in last_block
        assert "RME" not in last_block

    def test_last_played_just_now_label(self):
        zones = [_make_zone("Speakers", state="stopped")]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "Track", "artist": "A", "album": "B", "seconds_ago": 5,
            },
        }
        result = build_compact_zone_status(
            zones, {}, None, last_played_by_zone_id=last_played,
        )
        assert "(just now)" in result

    def test_last_played_time_marker_on_playing_zone_entry(self):
        """When a zone is playing, its LAST PLAYED entry refers to the
        track immediately before the current one. The time marker still
        appears for consistency."""
        zones = [_make_zone(
            "Speakers",
            state="playing",
            now_playing_lines=["Now", "Artist", "Album"],
            track_length=180,
        )]
        last_played = {
            zones[0]["zone_id"]: {
                "title": "Prev", "artist": "A", "album": "B",
                "seconds_ago": 200,
            },
        }
        result = build_compact_zone_status(
            zones, {}, None, last_played_by_zone_id=last_played,
        )
        last_block = result.split("LAST PLAYED:")[1]
        assert '"Prev — A — B" (3 min ago)' in last_block


class TestZoneRenderingDetails:
    """Volume, shuffle/repeat, and zone-level identifiers stay
    correct after the structural reformat."""

    def test_single_zone_playing_has_track_metadata(self):
        zones = [_make_zone(
            "MDAC+ USB",
            state="playing",
            now_playing_lines=["Song Title", "Artist Name", "Album Name"],
            track_length=225,
            outputs=[_make_output("MDAC+ USB", volume_value=45)],
        )]
        result = build_compact_zone_status(zones, {}, None)
        assert '"Song Title — Artist Name — Album Name" (3:45)' in result
        assert "Volume: 45%" in result
        assert "Shuffle: off" in result
        assert "Repeat: off" in result

    def test_grouped_zone_per_output_volume(self):
        zones = [_make_zone(
            "Chord Qutest + 1",
            state="paused",
            now_playing_lines=["Track", "Artist", "Album"],
            track_length=180,
            outputs=[
                _make_output("Chord Qutest", volume_value=60),
                _make_output("RME ADI-2 DAC FS", volume_type=None),
            ],
        )]
        result = build_compact_zone_status(zones, {}, None)
        assert "[GROUPED: Chord Qutest, RME ADI-2 DAC FS]" in result
        assert (
            "Volume: Chord Qutest: 60% | RME ADI-2 DAC FS: not controllable (fixed)"
            in result
        )

    def test_muted_volume(self):
        zones = [_make_zone(
            "TestZone",
            state="stopped",
            outputs=[_make_output("TestZone", volume_value=45, is_muted=True)],
        )]
        result = build_compact_zone_status(zones, {}, None)
        assert "Volume: 45% (muted)" in result

    def test_ghost_zones_filtered(self):
        ghost_zone = {
            "zone_id": "ghost",
            "display_name": "Ghost",
            "state": "stopped",
            "outputs": [],
        }
        real_zone = _make_zone("Real Zone", state="stopped")
        result = build_compact_zone_status([ghost_zone, real_zone], {}, None)
        assert "Ghost" not in result
        assert "Real Zone" in result

    def test_shuffle_on_repeat_loop(self):
        zones = [_make_zone(
            "TestZone",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=200,
            shuffle=True,
            loop="loop",
        )]
        result = build_compact_zone_status(zones, {}, None)
        assert "Shuffle: on" in result
        assert "Repeat: loop" in result

    def test_track_length_under_a_minute(self):
        zones = [_make_zone(
            "TestZone",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=65,
        )]
        result = build_compact_zone_status(zones, {}, None)
        assert "(1:05)" in result

    def test_no_zones_returns_fallback(self):
        result = build_compact_zone_status([], {}, None)
        assert "No zones" in result or result == ""

    def test_multiple_zones_all_included(self):
        zones = [
            _make_zone("Zone A", state="playing",
                       now_playing_lines=["Song A", "Artist A", "Album A"],
                       track_length=180),
            _make_zone("Zone B", state="stopped"),
            _make_zone("Zone C", state="paused",
                       now_playing_lines=["Song C", "Artist C", "Album C"],
                       track_length=240),
        ]
        result = build_compact_zone_status(zones, {}, None)
        assert "Zone A" in result
        assert "Zone B" in result
        assert "Zone C" in result
        # Mix of state markers shows up correctly.
        assert "[STOPPED]" in result
        assert "[PAUSED]" in result


class TestMalformedZonePayloads:
    """Production code defensively handles partial / malformed zone
    payloads (Roon emits these during transient states — e.g. a zone
    that's just woken from standby, or one mid-rename). These tests
    pin the 'must not crash, must produce something readable'
    contract for each defensive default."""

    def test_zone_missing_display_name_renders_unknown(self):
        zone = {
            "zone_id": "z1",
            "state": "stopped",
            "outputs": [_make_output("Out1", volume_value=50)],
        }
        result = build_compact_zone_status([zone], {}, None)
        assert "Unknown" in result

    def test_zone_missing_state_renders_with_no_marker(self):
        """``state`` missing → defaults to 'unknown'. The line still
        renders; the LLM sees the zone exists but its state is unclear."""
        zone = {
            "zone_id": "z1",
            "display_name": "Z1",
            "outputs": [_make_output("Out1", volume_value=50)],
        }
        result = build_compact_zone_status([zone], {}, None)
        # Zone is listed; state shows as the [UNKNOWN] marker.
        assert "Z1" in result
        assert "[UNKNOWN]" in result.upper()

    def test_now_playing_missing_three_line_uses_unknown_lines(self):
        """``now_playing`` present but ``three_line`` missing — each line
        falls back to 'Unknown' rather than crashing on a None lookup."""
        zone = {
            "zone_id": "z1",
            "display_name": "Z1",
            "state": "playing",
            "outputs": [_make_output("Out1", volume_value=50)],
            "now_playing": {"length": 200},
        }
        result = build_compact_zone_status([zone], {}, None)
        # Three "Unknown"s joined by em dashes inside the quoted block.
        assert '"Unknown — Unknown — Unknown"' in result

    def test_now_playing_three_line_partial_fills_missing(self):
        """``three_line`` present but lines 2/3 are None — only the
        present line shows; missing ones render as 'Unknown'."""
        zone = {
            "zone_id": "z1",
            "display_name": "Z1",
            "state": "playing",
            "outputs": [_make_output("Out1", volume_value=50)],
            "now_playing": {
                "three_line": {"line1": "Track", "line2": None, "line3": None},
                "length": 200,
            },
        }
        result = build_compact_zone_status([zone], {}, None)
        assert "Track" in result
        assert "Unknown" in result

    def test_volume_value_none_renders_unknown(self):
        zone = _make_zone(
            "Z1", state="stopped",
            outputs=[_make_output("Out1", volume_value=None)],
        )
        result = build_compact_zone_status([zone], {}, None)
        assert "unknown" in result.lower()

    def test_track_length_none_omits_duration_suffix(self):
        zone = _make_zone(
            "Z1", state="playing",
            now_playing_lines=["Track", "Artist", "Album"],
            track_length=None,
        )
        result = build_compact_zone_status([zone], {}, None)
        # No "(0:00)" or "(:00)" — the duration suffix is suppressed
        # entirely when length is None.
        assert "(0:" not in result
        assert "(:" not in result


class TestCompactPlaybackStatus:
    """Tool-output variant: same sectioned format as the context
    provider, with seek positions added to CURRENT STATUS entries."""

    def test_includes_seek_position(self):
        zones = [_make_zone(
            "TestZone",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=200,
            seek_position=65,
        )]
        result = build_compact_playback_status(zones, {}, None)
        assert "Position: 1:05 / 3:20" in result

    def test_multiple_zones(self):
        zones = [
            _make_zone("Zone A", state="playing",
                       now_playing_lines=["Song A", "Artist A", "Album A"],
                       track_length=180, seek_position=30),
            _make_zone("Zone B", state="stopped"),
            _make_zone("Zone C", state="paused",
                       now_playing_lines=["Song C", "Artist C", "Album C"],
                       track_length=240, seek_position=120),
        ]
        result = build_compact_playback_status(zones, {}, None)
        assert "Zone A" in result
        assert "Zone B" in result
        assert "Zone C" in result
        assert "Position: 0:30 / 3:00" in result
        assert "Position: 2:00 / 4:00" in result

    def test_no_seek_in_context_provider(self):
        """Context provider must NOT include seek; playback status must."""
        zones = [_make_zone(
            "TestZone",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=200,
            seek_position=65,
        )]
        context_result = build_compact_zone_status(zones, {}, None)
        playback_result = build_compact_playback_status(zones, {}, None)
        assert "Position:" not in context_result
        assert "Position:" in playback_result

    def test_stopped_zone_no_seek(self):
        zones = [_make_zone("TestZone", state="stopped", seek_position=10)]
        result = build_compact_playback_status(zones, {}, None)
        assert "Position" not in result

    def test_empty_zones(self):
        result = build_compact_playback_status([], {}, None)
        assert "No zones" in result

    def test_volume_always_present(self):
        zones = [_make_zone(
            "TestZone",
            state="stopped",
            outputs=[_make_output("TestZone", volume_type=None)],
        )]
        result = build_compact_playback_status(zones, {}, None)
        assert "not controllable (fixed)" in result

    def test_same_sectioned_format_as_context_provider(self):
        """The playback-status tool output uses the same section
        layout as the context provider; only seek inclusion differs."""
        zones = [_make_zone(
            "Speakers",
            state="playing",
            now_playing_lines=["Song", "Artist", "Album"],
            track_length=180,
            seek_position=30,
        )]
        result = build_compact_playback_status(zones, {}, "Speakers")
        assert "CURRENT STATUS:" in result
        assert "[DEFAULT ZONE]" in result
