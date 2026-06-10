"""Stop-marker coordinator: cached state + dispatch logic for the
silent-marker stop feature.

Reserves a dedicated browse session at construction so a steady-state
``dispatch_stop`` costs exactly two browse calls (drill the cached
track item → dispatch Play Now) rather than the search + drill chain
the original ``perform_stop`` ran on every invocation.

State machine:

* **disabled** — coordinator is inert. ``dispatch_stop`` always returns
  the pause-fallback signal; ``initialise`` does nothing. Driven by the
  ``DISABLE_SIMULATED_STOP`` env var (caller-provided at construction).
* **enabled, unavailable** — marker file isn't in the user's library
  (or hasn't been verified yet). ``dispatch_stop`` attempts a single
  re-init; on success it proceeds, on failure it returns the
  pause-fallback signal.
* **enabled, available** — cached ``track_item_key`` reaches the
  action_list directly. Each ``dispatch_stop`` is two browse calls in
  the steady state. On any failure: single re-init + single retry.

The Play Now action is *not* cached: empirically the Roon Core
invalidates the action_list on dispatch (the session pops back one
level). Caching only the track-level key — which Roon keeps stable
across calls on a non-popped session — is what makes steady-state
stops fast while still recovering correctly when the cache goes
stale.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

_log = logging.getLogger("swarpius.stop_marker")


@dataclass(frozen=True)
class StopResult:
    """Outcome of :meth:`StopMarkerCoordinator.dispatch_stop`.

    Three terminal shapes:

    * ``succeeded=True`` — real stop executed; caller need do nothing.
    * ``use_pause_fallback=True`` — caller should issue ``pause`` on
      this zone instead. Covers disabled mode and unavailable-marker
      cases. No banner.
    * ``succeeded=False, use_pause_fallback=False, error=<msg>`` —
      stop was attempted but failed terminally. Caller should surface
      the error to the user (banner). No pause issued; the user gets
      to know the stop didn't work.
    """

    succeeded: bool
    use_pause_fallback: bool
    error: Optional[str] = None


class StopMarkerCoordinator:
    def __init__(
        self,
        connection: Any,
        marker_title: str,
        broadcast_state: Callable[[], None],
        disabled: bool = False,
    ) -> None:
        self._connection = connection
        self._marker_title = marker_title
        self._broadcast_state = broadcast_state
        self._disabled = disabled
        self._available = False
        self._track_item_key: Optional[str] = None
        self._init_lock = threading.Lock()

    # ── Read-only state ───────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def track_item_key(self) -> Optional[str]:
        return self._track_item_key

    @property
    def session_key(self) -> str:
        return self._connection.session_manager.stop_session_key

    # ── initialise() ──────────────────────────────────────────────

    def initialise(self) -> bool:
        """Walk marker title → action_list and cache the post-wrapper
        item_key. Sets ``available``, broadcasts on flip. Returns the
        new ``available`` state. Returns False without doing anything when disabled."""
        if self._disabled:
            return False

        with self._init_lock:
            previous = self._available
            try:
                track_item_key = self._discover_marker()
            except Exception as exc:
                _log.warning("Stop-marker init failed: %s", exc)
                track_item_key = None

            self._track_item_key = track_item_key
            self._available = track_item_key is not None
            if self._available != previous:
                self._broadcast_state()
            return self._available

    def _discover_marker(self) -> Optional[str]:
        sk = self.session_key
        target = self._marker_title.strip().lower()

        search = self._connection.browse_core(
            aux={"pop_all": True, "input": self._marker_title},
            session_key=sk,
        )
        marker = next(
            (
                it for it in (search.items or [])
                if it.title and it.title.strip().lower() == target
            ),
            None,
        )
        if marker is None:
            return None

        # Drill the marker. If Roon inserts a single same-titled child
        # (the N=1 sources/versions wrapper), drill through it so the
        # cached key reaches the action_list directly — otherwise every
        # steady-state stop pays a redundant wrapper drill.
        drill = self._connection.browse_core(
            aux={"item_key": marker.item_key},
            session_key=sk,
        )
        track_item_key = marker.item_key
        if self._is_wrapper(marker, drill):
            wrapper = drill.items[0]
            track_item_key = wrapper.item_key
            drill = self._connection.browse_core(
                aux={"item_key": wrapper.item_key},
                session_key=sk,
            )

        play_now = self._connection.find_item_by_field(
            items=drill.items or [],
            field_name="title",
            field_value="Play Now",
        )
        if play_now is None:
            return None
        return track_item_key

    @staticmethod
    def _is_wrapper(parent_item: Any, results: Any) -> bool:
        items = results.items or []
        return (
            len(items) == 1
            and items[0].title == parent_item.title
            and items[0].hint == parent_item.hint
            and items[0].hint != "Action"
        )

    # ── dispatch_stop() ───────────────────────────────────────────

    def dispatch_stop(self, zone: Optional[str]) -> StopResult:
        """Drill cached track key → dispatch Play Now → on terminal
        success only, disable auto-radio.

        On any dispatch failure: one re-init, one retry. If re-init
        reports the marker is gone, returns the pause-fallback signal.
        If re-init succeeds but the retry still fails, returns a
        terminal error (banner-worthy) — the user gets to know the
        stop didn't work rather than silently degrading to pause.

        Limitation — Roon's silent-success contract: if the marker
        *file* disappears (uninstalled mid-uptime, network share gone)
        while the track *metadata* is still in Roon's library index,
        the dispatch returns OK and Roon plays nothing. We have no
        error to react to, so the cache stays warm and subsequent
        stops keep silently failing. The retry plumbing still covers
        any case where Roon *does* raise (network error, item_key
        invalidated by a re-scan, etc.) — it's the silent path that's
        unrecoverable without polling or playback-event observation,
        both of which we deliberately don't do. Documented in
        docs/known-limitations.md.
        """
        if self._disabled:
            return StopResult(succeeded=False, use_pause_fallback=True)

        cached_error: Optional[str] = None
        if self._available and self._track_item_key:
            cached_error = self._try_dispatch(zone)
            if cached_error is None:
                self._after_successful_dispatch(zone)
                return StopResult(succeeded=True, use_pause_fallback=False)

        # Recovery: one re-init, one retry.
        self.initialise()
        if not self._available or not self._track_item_key:
            return StopResult(succeeded=False, use_pause_fallback=True)

        retry_error = self._try_dispatch(zone)
        if retry_error is None:
            self._after_successful_dispatch(zone)
            return StopResult(succeeded=True, use_pause_fallback=False)

        msg = retry_error if cached_error is None else (
            f"{cached_error}; after re-init: {retry_error}"
        )
        return StopResult(succeeded=False, use_pause_fallback=False, error=msg)

    def _try_dispatch(self, zone: Optional[str]) -> Optional[str]:
        sk = self.session_key
        try:
            action_list = self._connection.browse_core(
                aux={"item_key": self._track_item_key},
                zone=zone,
                session_key=sk,
            )
            play_now = self._connection.find_item_by_field(
                items=action_list.items or [],
                field_name="title",
                field_value="Play Now",
            )
            if play_now is None:
                return "Play Now action not found in stop-marker action_list"
            self._connection.browse_core(
                aux={"item_key": play_now.item_key},
                zone=zone,
                session_key=sk,
            )
        except Exception as exc:
            return str(exc)
        return None

    def _after_successful_dispatch(self, zone: Optional[str]) -> None:
        # set_auto_radio is best-effort: the silent track has already
        # been queued on the zone, so a failure here only means the
        # post-silence radio behaviour may not be suppressed. Don't
        # downgrade an otherwise-successful stop on this account.
        try:
            self._connection.set_auto_radio(auto_radio=False, zone=zone)
        except Exception as exc:
            _log.warning(
                "Stop-marker dispatched but set_auto_radio(False) "
                "failed for zone=%r: %s", zone, exc,
            )
