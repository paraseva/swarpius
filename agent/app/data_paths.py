"""Centralised data directory resolution.

All mutable data lives under a single configurable root directory.
Set ``SWARPIUS_DATA_DIR`` to override the default (``<agent_root>/data/``).

This module reads ``SWARPIUS_DATA_DIR`` directly from ``os.environ``
rather than via ``app.settings`` — path helpers are called from
module-import paths before ``Settings`` is built, so they can't depend
on the locked-at-startup cache. The carve-out is intentional and
limited to this one variable.
"""

import os
import shutil
import sys
from pathlib import Path

AGENT_ROOT: Path = Path(__file__).resolve().parent.parent


def _bundled_user_data_dir() -> Path:
    """Per-platform user-data dir for the PyInstaller bundle.

    AGENT_ROOT in a bundle resolves to the extraction directory
    (treated as read-only for the bundle's own files and wiped on
    reinstall in one-folder mode), so the source-mode default
    ``AGENT_ROOT / "data"`` is wrong for bundles. Use the platform's
    canonical user-data location instead.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Swarpius"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Swarpius"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "swarpius"


def _running_from_bundle() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


# Set by the supervisor (swarpius.py) on every respawn after the first
# launch, so a restarted bundle can tell a Restart from a cold
# start.
RESTART_RESPAWN_ENV = "SWARPIUS_RESTART_RESPAWN"


def should_auto_open_browser() -> bool:
    """Whether to open the user's browser on this launch.

    Only on a cold bundle start. Source mode never auto-opens (dev runs
    the Vite server on :5173); a restart respawn doesn't either — the
    already-open tab reconnects over its WebSocket once the agent is back,
    so a fresh tab would just pile up.
    """
    return _running_from_bundle() and not os.environ.get(RESTART_RESPAWN_ENV)


def _running_in_docker() -> bool:
    """True when the agent runs inside a Docker container.

    Detected via the ``/.dockerenv`` marker file Docker creates in
    every container. Other runtimes (podman, containerd) aren't
    auto-detected; users on those can set ``SWARPIUS_IN_DOCKER=1``
    to force the flag (the Settings UI uses this to switch into
    read-only mode — the host-side ``.env`` isn't mounted into the
    container, so saves can't persist).
    """
    if Path("/.dockerenv").is_file():
        return True
    return os.environ.get("SWARPIUS_IN_DOCKER", "").strip().lower() in ("1", "true", "yes")


def app_version() -> str:
    """Single source of truth for the app version: ``agent/VERSION``,
    bundled at the agent root. Falls back to ``0.0.0`` if unreadable
    (e.g. a stripped checkout)."""
    try:
        return (AGENT_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def data_dir() -> Path:
    """Return the resolved data root directory."""
    raw = os.environ.get("SWARPIUS_DATA_DIR", "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else AGENT_ROOT / p
    if _running_from_bundle():
        return _bundled_user_data_dir()
    return AGENT_ROOT / "data"


# Name of the packaged folder the user copies into their Roon library.
# It contains the silent track; matched in Roon by track title, so this
# folder name is cosmetic — kept descriptive so it reads sensibly as an
# album once scanned. The non-bundle README references the same name.
STOP_MARKER_ASSET_NAME = "Swarpius Stop Simulation"


def stop_marker_staging_dir() -> Path:
    """Folder the bundle opens in the OS file manager. It contains the
    single copyable ``STOP_MARKER_ASSET_NAME`` folder (and nothing else),
    so the user can drag that whole folder into a Roon-watched location
    without being dropped into the raw data dir."""
    return data_dir() / "Stop Simulation"


def _stop_marker_asset_source() -> Path | None:
    """Locate the packaged stop-marker folder. Bundle →
    ``<_MEIPASS>/assets/<name>``; source layout → ``<repo>/assets/<name>``.
    Returns None when neither exists."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "assets" / STOP_MARKER_ASSET_NAME
        if bundled.is_dir():
            return bundled
    source = AGENT_ROOT.parent / "assets" / STOP_MARKER_ASSET_NAME
    return source if source.is_dir() else None


def ensure_stop_marker_asset() -> None:
    """Seed the staging dir with the packaged stop-marker folder on a
    bundle launch, so first-run users have the silent track locally
    (ready to drop into a Roon-watched folder) without fetching it from
    the source repo. N/A in source mode — the repo copy is canonical —
    and never overwrites an existing destination, so a user's own copy
    is left untouched."""
    if not _running_from_bundle():
        return
    dest = stop_marker_staging_dir() / STOP_MARKER_ASSET_NAME
    if dest.exists():
        return
    src = _stop_marker_asset_source()
    if src is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


def config_dir() -> Path:
    return data_dir() / "config"


def conversation_logs_dir() -> Path:
    return data_dir() / "logs" / "conversation"


def server_logs_dir() -> Path:
    return data_dir() / "logs" / "server"


def analysis_dir() -> Path:
    return data_dir() / "analysis"


def feedback_archive_dir() -> Path:
    return analysis_dir() / "feedback"


def messages_db_path() -> Path:
    return data_dir() / "messages.db"


def play_history_path() -> Path:
    return data_dir() / "play_history.json"


def cli_history_path() -> Path:
    return data_dir() / "cli_history"


def default_log_path() -> Path:
    return data_dir() / "logs" / "swarpius.log"


def ensure_dirs() -> None:
    """Create all data directories. Call once at startup."""
    for d in [config_dir(), conversation_logs_dir(), server_logs_dir(), analysis_dir(), feedback_archive_dir()]:
        d.mkdir(parents=True, exist_ok=True)
