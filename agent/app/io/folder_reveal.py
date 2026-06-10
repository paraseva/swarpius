"""Reveal the stop-marker folder in the OS file manager.

Bundle-only convenience: the desktop app and the user's browser share a
machine, so the agent can pop open the folder holding the silent
stop-marker track for the user to drag into a Roon-watched location.
Refused in source / Docker / headless modes, where there's either no
co-located file manager or shelling out on behalf of a remote client
would be a hazard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app import data_paths


def _default_opener(path: Path, platform: str) -> None:
    if platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def open_stop_marker_folder(
    *,
    platform: str = sys.platform,
    opener: Optional[Callable[[Path, str], None]] = None,
) -> Dict[str, Any]:
    """Reveal the stop-marker folder in the OS file manager.

    Returns ``{"ok": bool, "error": str | None}``. Honoured only on a
    desktop bundle launch; source / Docker get ``ok=False`` so the caller
    stays quiet rather than spawning a file manager on a headless host or
    on behalf of a remote LAN client. Always targets the fixed
    stop-marker folder — no caller-supplied path is honoured.
    """
    if not data_paths._running_from_bundle() or data_paths._running_in_docker():
        return {"ok": False, "error": "unavailable"}
    dest = data_paths.stop_marker_staging_dir()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        (opener or _default_opener)(dest, platform)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": None}
