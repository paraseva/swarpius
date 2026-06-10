"""Tests for the per-output play-history store.

Contract: tracks are recorded per output, not per zone. When outputs
are grouped, the played track is appended to every member output's
deque so the history follows the output through group/ungroup
cycles. The currently-playing entry is filtered out of
``get_last_played_dict`` per output — for a grouped zone, both
outputs independently report the previous track when asked.
The configured silent stop-marker is never recorded. The on-disk
file is versioned; a legacy v1 (zone-keyed) file is silently
discarded on load.
"""

from __future__ import annotations

import json
from typing import Any

from app.roon.play_history import PlayHistoryStore
from app.settings import get_settings

STOP_MARKER_TITLE = get_settings().stop_marker_title


def _state_payload(zones: list[dict]) -> dict:
    return {
        "type": "state",
        "event": "zones_changed",
        "zones": zones,
    }


def _zone(
    zone_id: str,
    outputs: list[str] | None = None,
    line1: str | None = None,
    line2: str | None = None,
    line3: str | None = None,
) -> dict[str, Any]:
    """Build a zone payload.

    ``outputs`` lists the output_ids in this zone (single-output by
    default). ``line1``/``line2``/``line3`` populate the now_playing
    three_line; omit all three for the idle/stopped shape.
    """
    if outputs is None:
        outputs = [f"{zone_id}_out"]
    payload: dict[str, Any] = {
        "zone_id": zone_id,
        "outputs": [{"output_id": oid} for oid in outputs],
    }
    if line1 is None and line2 is None and line3 is None:
        payload["now_playing"] = None
    else:
        payload["now_playing"] = {
            "three_line": {"line1": line1, "line2": line2, "line3": line3},
        }
    return payload


def _make_store(
    tmp_path,
    *,
    clock=None,
    max_per_zone=50,
    save_debounce_seconds=0.0,
    stop_marker_title=STOP_MARKER_TITLE,
):
    return PlayHistoryStore(
        store_path=tmp_path / "play_history.json",
        max_per_zone=max_per_zone,
        clock=clock or (lambda: 100.0),
        save_debounce_seconds=save_debounce_seconds,
        stop_marker_title=stop_marker_title,
    )


class TestStateEventTracking:
    def test_first_track_starts_pushes_to_each_output(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A",
                  line2="Artist A", line3="Album A"),
        ]))
        history = store.get_history("out_A")
        assert [e.title for e in history] == ["Song A"]
        assert history[0].artist == "Artist A"
        assert history[0].album == "Album A"
        assert history[0].played_at == 100.0

    def test_grouped_outputs_both_get_the_pushed_entry(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song X"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song X"]
        assert [e.title for e in store.get_history("out_B")] == ["Song X"]

    def test_track_transition_in_grouped_zone_pushes_to_all_outputs(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song X"),
        ]))
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song Y"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song X", "Song Y"]
        assert [e.title for e in store.get_history("out_B")] == ["Song X", "Song Y"]

    def test_metadata_refresh_does_not_push(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A", line2="Artist A"),
        ]))
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A", line2="ARTIST A"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song A"]

    def test_stop_does_not_push(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))
        store.handle_event(_state_payload([_zone("z1", outputs=["out_A"])]))
        assert [e.title for e in store.get_history("out_A")] == ["Song A"]

    def test_stop_marker_track_is_not_recorded(self, tmp_path):
        store = _make_store(tmp_path, stop_marker_title=STOP_MARKER_TITLE)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1=STOP_MARKER_TITLE),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song A"]

    def test_zone_with_no_outputs_skipped(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            {"zone_id": "z1", "outputs": [], "now_playing": {
                "three_line": {"line1": "Song A", "line2": None, "line3": None},
            }},
        ]))
        # No outputs → nothing recorded anywhere.
        assert store.get_history("any") == []

    def test_output_missing_output_id_skipped(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            {
                "zone_id": "z1",
                "outputs": [{"display_name": "Anonymous output"}],
                "now_playing": {"three_line": {"line1": "Song A"}},
            },
        ]))
        assert store.get_history("any") == []

    def test_per_output_isolation(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Solo A"),
            _zone("z2", outputs=["out_B"], line1="Solo B"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Solo A"]
        assert [e.title for e in store.get_history("out_B")] == ["Solo B"]

    def test_ungroup_keeps_each_outputs_history(self, tmp_path):
        store = _make_store(tmp_path)
        # Grouped: both outputs play Song X.
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song X"),
        ]))
        # Ungrouped: each output is now in its own solo zone. Solo A
        # plays Song Y; solo B is idle.
        store.handle_event(_state_payload([
            _zone("z_A", outputs=["out_A"], line1="Song Y"),
            _zone("z_B", outputs=["out_B"]),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song X", "Song Y"]
        # out_B was playing X under the group; ungrouping into an idle
        # zone records nothing new (push only fires on a new title).
        assert [e.title for e in store.get_history("out_B")] == ["Song X"]

    def test_radio_track_changes_push(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song 1", line2="Radiohead",
                  line3="Magic FM"),
        ]))
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song 2", line2="Beatles",
                  line3="Magic FM"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song 1", "Song 2"]

    def test_max_per_zone_caps_deque(self, tmp_path):
        store = _make_store(tmp_path, max_per_zone=3)
        for i in range(5):
            store.handle_event(_state_payload([
                _zone("z1", outputs=["out_A"], line1=f"Song {i}"),
            ]))
        history = store.get_history("out_A")
        assert [e.title for e in history] == ["Song 2", "Song 3", "Song 4"]

    def test_non_state_event_ignored(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event({"type": "queue", "zone_id": "z1"})
        assert store.get_history("any") == []


class TestMalformedEvents:
    def test_non_dict_payload_silently_ignored(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event("not a dict")  # type: ignore[arg-type]
        store.handle_event(None)  # type: ignore[arg-type]
        store.handle_event([])  # type: ignore[arg-type]
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="First"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["First"]

    def test_non_dict_zone_in_zones_list_skipped(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            "not a dict",
            None,
            _zone("z1", outputs=["out_A"], line1="Song A1"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Song A1"]

    def test_now_playing_missing_treated_as_idle(self, tmp_path):
        store = _make_store(tmp_path)
        idle_zone = {"zone_id": "z1", "outputs": [{"output_id": "out_A"}]}
        store.handle_event(_state_payload([idle_zone]))
        assert store.get_history("out_A") == []
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Real Track"),
        ]))
        assert [e.title for e in store.get_history("out_A")] == ["Real Track"]


class TestPersistence:
    def test_load_handles_missing_file(self, tmp_path):
        store = _make_store(tmp_path)
        store.load()
        assert store.get_last_played("any") is None

    def test_load_handles_corrupt_file(self, tmp_path):
        path = tmp_path / "play_history.json"
        path.write_text("not json", encoding="utf-8")
        store = PlayHistoryStore(
            store_path=path, save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        store.load()
        assert store.get_last_played("any") is None

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "play_history.json"
        store = PlayHistoryStore(
            store_path=path, clock=lambda: 100.0,
            save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song A"),
        ]))
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song B"),
        ]))
        store.save()

        reloaded = PlayHistoryStore(
            store_path=path, save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        reloaded.load()
        assert [e.title for e in reloaded.get_history("out_A")] == ["Song A", "Song B"]
        assert [e.title for e in reloaded.get_history("out_B")] == ["Song A", "Song B"]

    def test_save_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "play_history.json"
        store = PlayHistoryStore(
            store_path=path, save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))
        store.save()
        assert path.exists()

    def test_load_discards_unversioned_legacy_file(self, tmp_path):
        """The v1 on-disk format keyed by zone_id is dropped at load
        time — its entries map to zones that no longer exist as keys
        under the per-output schema."""
        path = tmp_path / "play_history.json"
        path.write_text(
            json.dumps({
                "some_zone_id": [
                    {"title": "Legacy Track", "artist": "A", "album": "B",
                     "ended_at": 42.0},
                ],
            }),
            encoding="utf-8",
        )
        store = PlayHistoryStore(
            store_path=path, save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        store.load()
        assert store.get_history("some_zone_id") == []

    def test_load_skips_invalid_entries(self, tmp_path):
        path = tmp_path / "play_history.json"
        path.write_text(
            json.dumps({
                "version": 2,
                "history": {
                    "out_A": [
                        {"title": "Good", "artist": "A", "album": "B",
                         "played_at": 1.0},
                        {"artist": "Missing title"},
                        "not a dict",
                    ],
                    "out_B": "not a list",
                },
            }),
            encoding="utf-8",
        )
        store = PlayHistoryStore(
            store_path=path, save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        store.load()
        history = store.get_history("out_A")
        assert len(history) == 1
        assert history[0].title == "Good"
        assert store.get_last_played("out_B") is None


class TestLastPlayedQuery:
    def test_last_played_skips_currently_playing_entry(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song B"),
        ]))
        d = store.get_last_played_dict("out_A")
        assert d is not None
        assert d["title"] == "Song A"

    def test_last_played_per_output_within_grouped_zone(self, tmp_path):
        """When outputs A and B are grouped and playing X, asking
        "what was the last track on A" must return the track before
        X — not X itself. Same for B."""
        store = _make_store(tmp_path)
        # Solo A's history before grouping.
        store.handle_event(_state_payload([
            _zone("z_A", outputs=["out_A"], line1="A's track"),
        ]))
        # Solo B's history before grouping.
        store.handle_event(_state_payload([
            _zone("z_A", outputs=["out_A"]),
            _zone("z_B", outputs=["out_B"], line1="B's track"),
        ]))
        # Group A+B; they start playing X.
        store.handle_event(_state_payload([
            _zone("z_group", outputs=["out_A", "out_B"], line1="Song X"),
        ]))
        # Both outputs are currently playing X. Previous track for A
        # is "A's track"; previous for B is "B's track".
        d_a = store.get_last_played_dict("out_A")
        d_b = store.get_last_played_dict("out_B")
        assert d_a is not None and d_a["title"] == "A's track"
        assert d_b is not None and d_b["title"] == "B's track"

    def test_last_played_returns_most_recent_when_idle(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song B"),
        ]))
        store.handle_event(_state_payload([_zone("z1", outputs=["out_A"])]))
        d = store.get_last_played_dict("out_A")
        assert d is not None
        assert d["title"] == "Song B"

    def test_last_played_returns_none_when_only_current_in_history(self, tmp_path):
        store = _make_store(tmp_path)
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))
        assert store.get_last_played_dict("out_A") is None

    def test_last_played_returns_none_for_unknown_output(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_last_played_dict("missing") is None

    def test_last_played_dict_includes_seconds_ago(self, tmp_path):
        clock = iter([100.0, 200.0, 250.0])
        store = PlayHistoryStore(
            store_path=tmp_path / "play_history.json",
            clock=lambda: next(clock),
            save_debounce_seconds=0.0,
            stop_marker_title=STOP_MARKER_TITLE,
        )
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song A"),
        ]))  # clock=100
        store.handle_event(_state_payload([
            _zone("z1", outputs=["out_A"], line1="Song B"),
        ]))  # clock=200
        d = store.get_last_played_dict("out_A")  # clock=250
        assert d is not None
        assert d["title"] == "Song A"
        assert d["played_at"] == 100.0
        assert d["seconds_ago"] == 150.0
