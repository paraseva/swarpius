"""Canonical .env file location, reader, and writer.

Path resolution differs by runtime mode:

- **PyInstaller bundle** — ``<data_dir>/.env`` (per-platform user-data
  location). The install directory may be read-only; user config
  lives in writable user-data so it survives reinstalls.
- **Source / Docker** — ``<agent>/.env``.

``ensure_env_file_exists()`` copies the bundled ``.env.template``
into the user-data location on first bundle launch.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Iterable, Optional

from dotenv import dotenv_values, load_dotenv, set_key, unset_key

from app.data_paths import (
    AGENT_ROOT,
    _running_from_bundle,
    _running_in_docker,
    data_dir,
)

log = logging.getLogger(__name__)


def resolve_env_path() -> Path:
    """Return the canonical path to the .env file for this runtime mode.

    Bundle: ``<data_dir>/.env`` (per-platform user-data location).
    Source / Docker: ``<agent>/.env``.
    """
    if _running_from_bundle():
        return data_dir() / ".env"
    return AGENT_ROOT / ".env"


def resolve_env_template_path() -> Optional[Path]:
    """Return the path to the bundled .env.template, if available.

    In a bundle the template lives at ``<_MEIPASS>/.env.template``.
    In source mode it lives at ``<agent>/.env.template``.
    Returns ``None`` if not found in either location.
    """
    import sys
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / ".env.template"
        if candidate.is_file():
            return candidate
    candidate = AGENT_ROOT / ".env.template"
    if candidate.is_file():
        return candidate
    return None


def ensure_env_file_exists() -> tuple[Path, bool]:
    """Ensure the canonical .env file exists, creating it from the
    bundled template on bundle first-run.

    Returns ``(env_path, was_just_created)``. Does nothing in source /
    Docker mode — devs create ``agent/.env`` themselves.
    """
    env_path = resolve_env_path()
    if env_path.exists():
        return env_path, False
    if not _running_from_bundle():
        return env_path, False

    template = resolve_env_template_path()
    if template is None:
        log.warning(
            "First-run bundle but no .env.template found to copy. "
            "User will need to create %s manually.", env_path,
        )
        return env_path, False

    env_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, env_path)
    log.info("Copied .env.template → %s for first-run setup", env_path)
    return env_path, True


def load_env_into_process() -> Path:
    """Resolve and load the .env into ``os.environ``. Returns the path
    that was loaded, or the path inspected if it didn't exist."""
    env_path = resolve_env_path()
    if env_path.exists():
        load_dotenv(env_path, override=False)
        log.debug("Loaded .env from %s", env_path)
    else:
        log.debug("No .env file at %s — using process env only", env_path)
    return env_path


def reload_env_into_process() -> Path:
    """Re-read the .env file, overriding existing ``os.environ`` values.
    Settings consumers that cache at startup won't see new values
    until restart — the UI labels this.
    """
    env_path = resolve_env_path()
    if env_path.exists():
        load_dotenv(env_path, override=True)
        log.info("Reloaded .env from %s", env_path)
    return env_path


def read_env_file() -> Dict[str, Optional[str]]:
    """Parse the canonical .env file and return its key/value mapping.

    Returns ``{}`` if the file doesn't exist. Pure parse — doesn't
    touch ``os.environ``. Values are strings, or ``None`` for keys
    present without a value.
    """
    env_path = resolve_env_path()
    if not env_path.exists():
        return {}
    return dict(dotenv_values(env_path))


def env_editable() -> bool:
    """Whether the canonical .env file can be edited from inside the
    agent process.

    False in Docker — the host file is bind-mounted read-only so the
    agent can't rewrite secrets on the host filesystem. Source /
    bundle: True.
    """
    return not _running_in_docker()


def resolve_env_path_for_display() -> str:
    """Path string to show in the Settings UI.

    Source / bundle: the canonical in-process path. Docker: the
    host-side path the user should edit (``agent/.env`` relative to
    the compose file) — the in-container path (``/app/.env``) is
    meaningless to a user editing on the host.
    """
    if _running_in_docker():
        return "agent/.env"
    return str(resolve_env_path())


def read_managed_env(managed_keys: Iterable[str]) -> Dict[str, Optional[str]]:
    """Return current values for UI-managed env vars.

    Source / bundle: reads ``read_env_file()`` (the .env file is the
    canonical persistence layer; comments and key ordering are
    preserved on write).

    Docker: filters ``os.environ`` to ``managed_keys`` — populated by
    ``load_env_into_process()`` at startup from the bind-mounted
    ``/app/.env``.
    """
    if _running_in_docker():
        return {k: os.environ.get(k) for k in managed_keys if k in os.environ}
    return read_env_file()


def write_env_file(updates: Dict[str, Optional[str]]) -> Path:
    """Apply ``updates`` to the .env file in place, preserving comments.

    Values of ``None`` or ``""`` remove the key. New keys are appended.
    Creates the parent directory and file if missing.
    """
    env_path = resolve_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()

    for key, value in updates.items():
        if value is None or value == "":
            unset_key(str(env_path), key, quote_mode="never")
        else:
            set_key(str(env_path), key, value, quote_mode="always")

    log.info("Wrote %d update(s) to %s", len(updates), env_path)
    return env_path
