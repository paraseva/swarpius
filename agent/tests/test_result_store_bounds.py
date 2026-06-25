"""Bounding contracts for caches that would otherwise grow unbounded.

1. ``result_store`` entries minted via ``store_result_handle()`` (the
   direct-write path that doesn't add a ``search_history`` entry) are
   capped — without a cap, long sessions leak.

2. ``image_base64_cache`` on RuntimeState is bounded. Each cache key
   encodes (image_key, width, height), so different sizes of the same
   image count as distinct entries.

3. ``analysis-history.yaml`` in each conversation dir is rotated —
   only the last N versions are kept.
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

ANALYSER_DIR = Path(__file__).resolve().parents[2] / "passive-analyser"
if str(ANALYSER_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSER_DIR))


# ------------------------------------------------------------------ #
#  store_result_handle bounded growth                          #
# ------------------------------------------------------------------ #

class TestStoreResultHandleBounded(unittest.TestCase):
    """Direct ``store_result_handle`` calls populate ``result_store``
    without adding to ``search_history``. Without a cap, long sessions
    leak memory."""

    def _bare_runtime(self):
        from app.runtime.result_store_manager import ResultStoreManager
        from app.runtime.state import RuntimeState
        rs = object.__new__(RuntimeState)
        rs.results = ResultStoreManager()
        rs.result_store = rs.results.entries
        rs.search_history = rs.results.history
        rs.result_store_lock = rs.results.lock
        return rs


    def test_store_result_handle_is_bounded(self):
        """Storing more handles than the cap leaves the result_store bounded to
        the cap, not growing one-per-call.

        Pin the cap to a canonical value so the contract holds regardless of any
        ``RESULT_STORE_MAX_ENTRIES`` in the ambient environment / ``.env``."""
        from app.settings import reset_settings_for_tests

        with patch.dict(os.environ, {"RESULT_STORE_MAX_ENTRIES": "50"}, clear=False):
            reset_settings_for_tests()
            rs = self._bare_runtime()
            for i in range(100):
                rs.store_result_handle({"i": i})
            self.assertLessEqual(
                len(rs.result_store), 50,
                f"result_store not bounded to its cap: {len(rs.result_store)} "
                "entries after 100 store_result_handle calls with cap 50",
            )


# ------------------------------------------------------------------ #
#  image caches bounded                                        #
# ------------------------------------------------------------------ #

class TestImageCachesBounded(unittest.TestCase):
    """Zone artwork caches grow with every new (image_key, width, height)
    triple. The cap is enforced via FIFO eviction in ``_BoundedDict``;
    this fixture mirrors how the real ``__init__`` constructs them."""

    def _bare_runtime(self):
        from app.roon.zone_artwork_service import ZoneArtworkCache
        from app.roon.zone_domain import ZoneDomain
        from app.runtime.state import RuntimeState
        from app.runtime.zones import ZoneSubsystem
        from app.settings import get_settings
        cap = get_settings().image_cache_max_entries
        rs = object.__new__(RuntimeState)
        rs.zones = ZoneSubsystem(
            domain=object.__new__(ZoneDomain),
            artwork=ZoneArtworkCache(max_entries=cap),
        )
        return rs

    def test_image_base64_cache_bounded(self):
        """Populating image_base64_cache past the cap evicts oldest, leaving it
        bounded to the cap.

        Pin the cap to a canonical value so the contract holds regardless of any
        ``IMAGE_CACHE_MAX_ENTRIES`` in the ambient environment / ``.env``."""
        from app.settings import reset_settings_for_tests

        with patch.dict(os.environ, {"IMAGE_CACHE_MAX_ENTRIES": "50"}, clear=False):
            reset_settings_for_tests()
            rs = self._bare_runtime()
            for i in range(200):
                rs.image_base64_cache[f"img_{i}:200:200"] = {
                    "data": f"fake-base64-{i}",
                    "mime": "image/jpeg",
                }
            self.assertLessEqual(
                len(rs.image_base64_cache), 50,
                f"image_base64_cache not bounded to its cap: "
                f"{len(rs.image_base64_cache)} entries with cap 50",
            )


# ------------------------------------------------------------------ #
#  analysis-history.yaml rotation                              #
# ------------------------------------------------------------------ #

class TestAnalysisHistoryRotated(unittest.TestCase):
    """Every re-analysis appends to analysis-history.yaml. Without a
    rotation cap, the file grows linearly in re-analysis count, making
    it slow to parse and large on disk."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.conv_dir = Path(self._tmp.name) / "2026-04-22" / "c01"
        self.conv_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _sample_analysis(self, version: str) -> dict:
        return {
            "analysed_at": f"2026-04-22T{version}:00:00Z",
            "conversation_id": "c01",
            "date": "2026-04-22",
            "version_marker": version,
            "findings": [],
        }


    def test_history_is_rotated_after_many_analyses(self):
        """Calling write_analysis past the cap rotates analysis-history.yaml,
        leaving it bounded rather than one entry per call.

        Pin the cap to a canonical value: the analyser reads
        ``ANALYSIS_HISTORY_MAX_ENTRIES`` into a module constant at import time
        (not via agent settings), so patch the constant directly to keep the
        test independent of the ambient environment."""
        from analyser import analyse

        # Seed with an initial analysis so each subsequent call snapshots.
        (self.conv_dir / "analysis.yaml").write_text(
            yaml.dump(self._sample_analysis("v0"), sort_keys=False),
            encoding="utf-8",
        )

        with patch.object(analyse, "ANALYSIS_HISTORY_MAX_ENTRIES", 20):
            for i in range(1, 51):  # 50 re-analyses, well past the cap
                analyse.write_analysis(self.conv_dir, self._sample_analysis(f"v{i}"))

        history_path = self.conv_dir / "analysis-history.yaml"
        history = yaml.safe_load(history_path.read_text(encoding="utf-8"))
        self.assertLessEqual(
            len(history), 20,
            f"analysis-history.yaml has {len(history)} entries with cap 20 — "
            "rotation cap not enforced",
        )


if __name__ == "__main__":
    unittest.main()
