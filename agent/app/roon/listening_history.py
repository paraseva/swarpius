"""Listening-history store — a queryable record of what played, when, where.

Listens to the Roon state-event stream and records each new track per zone
to the ``listening_history`` table, so the coordinator can answer time-ranged
questions ("what did I listen to last Tuesday"). This is distinct from
:class:`~app.roon.play_history.PlayHistoryStore`, which keeps a bounded
per-output "last played here" deque for live zone context.

Detection is per-zone: a track is recorded once when it becomes the active
title in a zone (same-title metadata refreshes are ignored), so a track on a
grouped zone is one entry, not one per output. The configured stop-marker
track is filtered out. Writes go straight to the shared state DB.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from app.io.state_db import StateDb

_log = logging.getLogger("app.roon.listening_history")


class ListeningHistoryStore:
    def __init__(
        self,
        state_db: StateDb,
        clock: Callable[[], float] = time.time,
        stop_marker_title: str = "",
    ) -> None:
        self._db = state_db
        self._clock = clock
        self._stop_marker_title = stop_marker_title
        self._last_seen: Dict[str, str] = {}  # zone_id → last recorded title
        self._lock = threading.RLock()

    def set_stop_marker_title(self, title: str) -> None:
        self._stop_marker_title = title

    # ── Event handling ────────────────────────────────────────────

    def handle_event(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict) or payload.get("type") != "state":
            return
        zones = payload.get("zones") or []
        if not isinstance(zones, list):
            return
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            self._maybe_record(zone)

    def _maybe_record(self, zone: Dict[str, Any]) -> None:
        zone_id = zone.get("zone_id")
        if not zone_id:
            return
        now_playing = zone.get("now_playing") or {}
        three_line = now_playing.get("three_line") or {}
        title = three_line.get("line1")
        if not title:
            return
        if self._stop_marker_title and title == self._stop_marker_title:
            return
        with self._lock:
            if self._last_seen.get(zone_id) == title:
                return
            self._last_seen[zone_id] = title

        duration = now_playing.get("length")
        try:
            self._record(
                zone=zone.get("display_name"),
                title=title,
                artist=three_line.get("line2"),
                album=three_line.get("line3"),
                duration=int(duration) if duration is not None else None,
            )
        except Exception:  # noqa: BLE001 — recording must never break event handling
            _log.warning("Failed to record listening history", exc_info=True)

    def _record(
        self,
        zone: Optional[str],
        title: str,
        artist: Optional[str],
        album: Optional[str],
        duration: Optional[int],
    ) -> None:
        ts_ms = int(self._clock() * 1000)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO listening_history (ts, zone, title, artist, album, duration) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts_ms, zone, title, artist, album, duration),
            )

    # ── Queries ────────────────────────────────────────────────────

    def query(
        self,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
        zone: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return recorded plays, newest first, within the optional time
        window / zone filter."""
        clauses: List[str] = []
        params: List[Any] = []
        if since_ms is not None:
            clauses.append("ts >= ?")
            params.append(since_ms)
        if until_ms is not None:
            clauses.append("ts <= ?")
            params.append(until_ms)
        if zone is not None:
            clauses.append("zone = ?")
            params.append(zone)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT ts, zone, title, artist, album, duration FROM listening_history"
                f"{where} ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            {
                "ts": ts,
                "zone": zone_name,
                "title": title,
                "artist": artist,
                "album": album,
                "duration": duration,
            }
            for ts, zone_name, title, artist, album, duration in rows
        ]

    def clear(self) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM listening_history")
        with self._lock:
            self._last_seen.clear()
