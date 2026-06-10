"""Shape-typed file parsers shared by the analysis browser and feedback modules.

These helpers swallow every parse / IO error and return a sentinel value (``None``
for dict-shaped files, ``[]`` for list-shaped files) so callers that walk many
log directories can ignore corrupt or absent files without scattering try/except
blocks. Errors are logged at DEBUG level for ad-hoc investigation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger("swarpius.parse_helpers")


def safe_parse_yaml(path: Path) -> dict[str, Any] | None:
    """Parse a YAML file expected to contain a dict, returning None on any error."""
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        _log.debug("Failed to parse %s", path, exc_info=True)
        return None


def safe_parse_yaml_list(path: Path) -> list[dict[str, Any]]:
    """Parse a YAML file expected to contain a list, returning [] on any error."""
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        _log.debug("Failed to parse %s", path, exc_info=True)
        return []
