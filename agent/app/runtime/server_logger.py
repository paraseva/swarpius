"""Server-side YAML logger for detailed browse/action traces.

One YAML file per request at ``logs/server/<date>/<cNN>/<request-id>/server.yaml``.
Each entry is a YAML document separated by ``---``.  The current request ID
(from contextvars) is embedded so entries can be cross-referenced with
conversation logs.

Usage in instrumented code::

    from app.runtime.server_logger import get_server_logger
    get_server_logger().log("resolve_ref", ref_id="00007", tier="key_live")
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from app.data_paths import server_logs_dir

_log = logging.getLogger("swarpius.server_logger")

_LOG_ROOT = server_logs_dir()


class ServerLogger:
    """Thread-safe YAML writer. One file per request directory."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or _LOG_ROOT
        self._lock = threading.Lock()
        self._current_path: Optional[Path] = None
        self._current_fh: Any = None
        self._request_id: Optional[str] = None

    def set_request_id(self, request_id: Optional[str]) -> None:
        self._request_id = request_id

    def log(self, op: str, **details: Any) -> None:
        try:
            self._write(op, **details)
        except Exception:  # noqa: BLE001
            _log.debug("ServerLogger.log failed for op=%s", op, exc_info=True)

    def _write(self, op: str, **details: Any) -> None:
        request_id = self._request_id
        conv_dir = self._extract_conv_dir(request_id)
        if not conv_dir:
            return

        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "request_id": request_id,
            "op": op,
        }
        entry.update(details)

        def _stringify(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _stringify(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_stringify(v) for v in obj]
            if not isinstance(obj, (str, int, float, bool, type(None))):
                return str(obj)
            return obj

        entry = _stringify(entry)

        target = self._root / datetime.now().strftime("%Y-%m-%d") / conv_dir / request_id / "server.yaml"

        with self._lock:
            if self._current_path != target:
                self._close()
                target.parent.mkdir(parents=True, exist_ok=True)
                self._current_fh = open(target, "a", encoding="utf-8")  # noqa: SIM115
                self._current_path = target
            # One write call so a kill mid-entry can't leave a partial
            # YAML document — append-only means older entries are
            # always intact, this contains the damage to the in-flight
            # entry itself.
            self._current_fh.write(
                "---\n"
                + yaml.dump(entry, default_flow_style=False, sort_keys=False)
                + "\n",
            )
            self._current_fh.flush()

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._current_fh:
            self._current_fh.close()
            self._current_fh = None
            self._current_path = None

    @staticmethod
    def _extract_conv_dir(request_id: Optional[str]) -> Optional[str]:
        if not request_id or not request_id.startswith("rq-"):
            return None
        parts = request_id.split("-")
        return parts[1] if len(parts) >= 3 else None


class NullServerLogger:
    """Do-nothing logger for tests."""

    def set_request_id(self, request_id: Optional[str]) -> None:
        pass

    def log(self, op: str, **details: Any) -> None:
        pass

    def close(self) -> None:
        pass


# Module-level singleton
_logger: ServerLogger | NullServerLogger = ServerLogger()


def get_server_logger() -> ServerLogger | NullServerLogger:
    return _logger


def set_server_logger(logger: ServerLogger | NullServerLogger) -> None:
    global _logger
    _logger = logger


def cleanup_old_server_logs(retention_days: Optional[int] = None) -> int:
    """Delete server log directories older than *retention_days*."""
    if retention_days is None:
        from app.settings import get_settings
        retention_days = get_settings().log_retention_days
    retention_days = max(retention_days, 1)

    root = _LOG_ROOT
    if not root.is_dir():
        return 0

    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    removed = 0
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
