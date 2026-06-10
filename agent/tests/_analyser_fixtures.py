"""Shared helpers for tests that exercise passive-analyser/analyse.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PASSIVE_ANALYSIS_DIR = Path(__file__).resolve().parent.parent.parent / "passive-analyser"
if str(_PASSIVE_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_PASSIVE_ANALYSIS_DIR))

from analyser import analyse  # noqa: E402


def install_temp_lessons_path(test_case: unittest.TestCase) -> Path:
    """Point ``analyse.LESSONS_PATH`` at a fresh tmp file for the test.

    Returns the path. Cleanup is registered via ``test_case.addCleanup``.
    """
    tmp = tempfile.TemporaryDirectory()
    test_case.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "lessons-learned.md"
    p = patch.object(analyse, "LESSONS_PATH", path)
    p.start()
    test_case.addCleanup(p.stop)
    return path
