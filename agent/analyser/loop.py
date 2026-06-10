"""Background analyser scan loop running as a daemon thread.

Spawned at agent startup when ``ENABLE_PASSIVE_ANALYSER=true``.
Periodically scans for unanalysed-and-stable conversations and runs
the same entry functions the UI buttons call, coordinated via the
shared ``filelock`` scan lock (see ``acquire_scan_lock`` in
``analyse.py``).

Dedicated thread (rather than ``asyncio.to_thread`` from a coroutine)
makes the "doesn't block the event loop" guarantee structural rather
than per-call-site discipline.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("analyser.loop")

# Delay before the first scan so the startup window stays snappy.
_FIRST_SCAN_DELAY_SECONDS = 60


def start_background_loop(
    stop_event: threading.Event,
    *,
    interval_minutes: int,
    staleness_minutes: int,
    batch_size: int,
) -> threading.Thread:
    """Spawn the analyser scan loop on a daemon thread.

    Setting ``stop_event`` exits the loop at its next wake point.
    Returned for tests to ``join()``.
    """
    thread = threading.Thread(
        target=_run_loop,
        name="swarpius-analyser-loop",
        kwargs={
            "stop_event": stop_event,
            "interval_minutes": interval_minutes,
            "staleness_minutes": staleness_minutes,
            "batch_size": batch_size,
        },
        daemon=True,
    )
    thread.start()
    return thread


def _run_loop(
    stop_event: threading.Event,
    *,
    interval_minutes: int,
    staleness_minutes: int,
    batch_size: int,
) -> None:
    """The actual loop body. Runs in the daemon thread."""
    from analyser.analyse import (
        AnalyserFatalError,
        acquire_scan_lock,
        collect_metrics,
        consolidate_lessons,
        prepare_context,
        process_all_pending_feedback,
        run_scan,
    )

    interval_seconds = max(1, interval_minutes * 60)

    log.info(
        "Analyser loop started — first scan in %ds, then every %d minute(s)",
        _FIRST_SCAN_DELAY_SECONDS, interval_minutes,
    )

    # Initial delay so the agent's port-bind / banner / browser-open
    # don't fight a hot scan for the first 60s of process life.
    if stop_event.wait(timeout=_FIRST_SCAN_DELAY_SECONDS):
        return

    while not stop_event.is_set():
        try:
            model, api_key, guide_text, git_ref = prepare_context()
        except AnalyserFatalError as exc:
            log.error(
                "Analyser configuration invalid — loop exiting: %s. "
                "Fix .env and restart the agent to resume.", exc,
            )
            return
        except Exception:
            log.exception("Unexpected error preparing analyser context — skipping this tick")
            if stop_event.wait(timeout=interval_seconds):
                return
            continue

        try:
            with acquire_scan_lock() as acquired:
                if not acquired:
                    log.debug("Scan lock contended — skipping this tick")
                else:
                    process_all_pending_feedback(model, api_key, guide_text, git_ref)
                    consolidate_lessons(model, api_key)
                    run_scan(model, api_key, guide_text, git_ref, staleness_minutes, batch_size)
                    collect_metrics()
        except AnalyserFatalError as exc:
            log.error(
                "Analyser hit a fatal error — loop exiting: %s. "
                "Fix the underlying issue and restart the agent.", exc,
            )
            return
        except Exception:
            log.exception("Unexpected error during scan — continuing")

        if stop_event.wait(timeout=interval_seconds):
            return

    log.info("Analyser loop stopped")
