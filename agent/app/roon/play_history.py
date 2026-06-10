"""Per-output play-history store.

Listens to the Roon state-event stream and records each new track as
soon as it starts playing. History is keyed by ``output_id`` so an
output's listening history follows the physical output through
group/ungroup cycles — when outputs are grouped and play a track,
the entry is appended to every member output's deque. The
configured silent stop-marker track is filtered out so a stop
action doesn't pollute the deque.

``get_last_played_dict`` skips the currently-playing entry per
output, so a grouped zone's outputs each report their own previous
track when asked. The on-disk file is versioned; an older
zone-keyed file is silently discarded on load.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

_log = logging.getLogger("app.roon.play_history")

_STORE_SCHEMA_VERSION = 2


@dataclass
class PlayHistoryEntry:
    title: str
    artist: Optional[str]
    album: Optional[str]
    played_at: float


class PlayHistoryStore:
    """Bounded per-output deque of previously-played tracks.

    Track transitions are detected by comparing the current
    ``now_playing.three_line.line1`` against the previously-observed
    value for each output. A new track is pushed at the moment its
    title becomes the active one; same-title metadata refreshes are
    ignored.
    """

    DEFAULT_MAX_PER_ZONE = 50
    DEFAULT_SAVE_DEBOUNCE_SECONDS = 5.0

    def __init__(
        self,
        store_path: Path,
        max_per_zone: int = DEFAULT_MAX_PER_ZONE,
        clock: Callable[[], float] = time.time,
        save_debounce_seconds: float = DEFAULT_SAVE_DEBOUNCE_SECONDS,
        stop_marker_title: str = "",
    ) -> None:
        self._store_path = store_path
        self._max_per_zone = max_per_zone
        self._clock = clock
        self._save_debounce_seconds = save_debounce_seconds
        self._stop_marker_title = stop_marker_title
        self._history: Dict[str, Deque[PlayHistoryEntry]] = {}
        # output_id → last observed (line1, line2, line3) tuple
        self._last_seen: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {}
        self._lock = threading.RLock()
        self._save_timer: Optional[threading.Timer] = None

    def set_stop_marker_title(self, title: str) -> None:
        """Update the stop-marker filter title after construction.

        Wired from ``RuntimeState._ensure_initialised_locked`` so the
        runtime's ``__init__`` does not have to read Settings (which
        would snapshot the cache before tests apply their env patches).
        """
        self._stop_marker_title = title

    # ── Event handling ────────────────────────────────────────────

    def handle_event(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict) or payload.get("type") != "state":
            return
        zones = payload.get("zones") or []
        if not isinstance(zones, list):
            return
        any_pushed = False
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            outputs = zone.get("outputs") or []
            if not isinstance(outputs, list):
                continue
            new_key = self._three_line_key(zone)
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                output_id = output.get("output_id")
                if not output_id:
                    continue
                if self._apply_for_output(output_id, new_key):
                    any_pushed = True

        if any_pushed:
            self._schedule_save()

    def _apply_for_output(
        self,
        output_id: str,
        new_key: Tuple[Optional[str], Optional[str], Optional[str]],
    ) -> bool:
        """Update last_seen + maybe push for one output. Returns True
        if a push happened so the caller can decide whether to save."""
        with self._lock:
            old_key = self._last_seen.get(output_id)
            self._last_seen[output_id] = new_key

        if not self._should_push(old_key, new_key):
            return False
        entry = PlayHistoryEntry(
            title=new_key[0] or "",
            artist=new_key[1],
            album=new_key[2],
            played_at=self._clock(),
        )
        with self._lock:
            dq = self._history.setdefault(
                output_id, deque(maxlen=self._max_per_zone),
            )
            dq.append(entry)
        return True

    @staticmethod
    def _three_line_key(
        zone: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        now_playing = zone.get("now_playing") or {}
        three_line = now_playing.get("three_line") or {}
        return (
            three_line.get("line1"),
            three_line.get("line2"),
            three_line.get("line3"),
        )

    def _should_push(
        self,
        old_key: Optional[Tuple[Optional[str], Optional[str], Optional[str]]],
        new_key: Tuple[Optional[str], Optional[str], Optional[str]],
    ) -> bool:
        new_title = new_key[0]
        if not new_title:
            return False
        if self._stop_marker_title and new_title == self._stop_marker_title:
            return False
        if old_key is not None and old_key[0] == new_title:
            return False
        return True

    # ── Queries ────────────────────────────────────────────────────

    def get_last_played(self, output_id: str) -> Optional[PlayHistoryEntry]:
        with self._lock:
            dq = self._history.get(output_id)
            if not dq:
                return None
            return dq[-1]

    def get_last_played_dict(self, output_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            dq = self._history.get(output_id)
            if not dq:
                return None
            current_title = self._last_seen.get(output_id, (None, None, None))[0]
            if current_title and dq[-1].title == current_title:
                if len(dq) < 2:
                    return None
                entry = dq[-2]
            else:
                entry = dq[-1]
        seconds_ago = max(0.0, self._clock() - entry.played_at)
        return {
            "title": entry.title,
            "artist": entry.artist,
            "album": entry.album,
            "played_at": entry.played_at,
            "seconds_ago": seconds_ago,
        }

    def get_history(self, output_id: str) -> List[PlayHistoryEntry]:
        with self._lock:
            dq = self._history.get(output_id)
            return list(dq) if dq else []

    # ── Persistence ───────────────────────────────────────────────

    def load(self) -> None:
        if not self._store_path.exists():
            return
        try:
            raw = self._store_path.read_text(encoding="utf-8").strip()
            data = json.loads(raw) if raw else {}
        except (OSError, json.JSONDecodeError):
            _log.warning("Failed to load play history; starting empty.", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        if data.get("version") != _STORE_SCHEMA_VERSION:
            # An older schema (or a hand-edited file without a
            # version) is dropped: its keys can't be reused as
            # output_ids and a clean rebuild from live events is
            # cheaper than guessing a mapping.
            return
        history = data.get("history")
        if not isinstance(history, dict):
            return
        with self._lock:
            for output_id, entries in history.items():
                if not isinstance(entries, list):
                    continue
                dq: Deque[PlayHistoryEntry] = deque(maxlen=self._max_per_zone)
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    title = entry.get("title")
                    if not title:
                        continue
                    try:
                        played_at = float(entry.get("played_at") or 0.0)
                    except (TypeError, ValueError):
                        played_at = 0.0
                    dq.append(PlayHistoryEntry(
                        title=str(title),
                        artist=entry.get("artist"),
                        album=entry.get("album"),
                        played_at=played_at,
                    ))
                if dq:
                    self._history[str(output_id)] = dq

    def save(self) -> None:
        with self._lock:
            history = {
                output_id: [asdict(e) for e in dq]
                for output_id, dq in self._history.items()
                if dq
            }
        data = {"version": _STORE_SCHEMA_VERSION, "history": history}
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(self._store_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._store_path)

    def _schedule_save(self) -> None:
        if self._save_debounce_seconds <= 0:
            self.save()
            return
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(self._save_debounce_seconds, self._safe_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _safe_save(self) -> None:
        try:
            self.save()
        except Exception:
            _log.warning("Failed to persist play history", exc_info=True)

    def flush(self) -> None:
        """Cancel pending debounced save and write immediately."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
        self._safe_save()
