"""The analyser resolves its data dir bundle-correctly and skips git in a bundle.

In a frozen bundle, conversation logs live under the per-platform
user-data dir (via app.data_paths), not AGENT_DIR/data — the analyser
must read from the same place the request logger writes, or "Scan &
Analyse" finds nothing. And invoking git in a bundle pops the macOS
"install command line tools" dialog, so it must be skipped there.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from analyser import analyse, metrics  # noqa: E402
from app import data_paths  # noqa: E402


def _frozen():
    return patch.multiple(sys, frozen=True, _MEIPASS="/fake/meipass", create=True)


class TestAnalyserDataDirBundleAware(unittest.TestCase):
    def test_analyse_data_dir_matches_canonical_when_frozen(self):
        with _frozen(), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SWARPIUS_DATA_DIR", None)
            # Per-platform user-data dir (where conversations are written),
            # not the read-only AGENT_DIR/data.
            self.assertEqual(analyse._data_dir(), data_paths.data_dir())
            self.assertNotEqual(analyse._data_dir(), analyse.AGENT_DIR / "data")

    def test_metrics_data_dir_matches_canonical_when_frozen(self):
        with _frozen(), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SWARPIUS_DATA_DIR", None)
            self.assertEqual(metrics._data_dir(), data_paths.data_dir())


class TestGitRefSkippedInBundle(unittest.TestCase):
    def test_returns_none_and_does_not_invoke_git_when_frozen(self):
        with _frozen(), patch.object(analyse.subprocess, "run") as run:
            self.assertIsNone(analyse.get_git_ref())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
