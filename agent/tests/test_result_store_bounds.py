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

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
        """Calling store_result_handle 100x should not leave 100 entries
        in result_store — the cap (whatever it is) must be enforced."""
        rs = self._bare_runtime()
        for i in range(100):
            rs.store_result_handle({"i": i})
        # Post-refactor: result_store respects a cap. Using 50 as the
        # assertion target — the actual cap can be anywhere reasonable,
        # the test only requires boundedness.
        self.assertLess(
            len(rs.result_store), 100,
            f"result_store unbounded: {len(rs.result_store)} entries "
            "after 100 store_result_handle calls",
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
        """Populating image_base64_cache with many distinct keys must
        respect a cap (oldest / LRU evicted)."""
        rs = self._bare_runtime()
        for i in range(500):
            rs.image_base64_cache[f"img_{i}:200:200"] = {
                "data": f"fake-base64-{i}",
                "mime": "image/jpeg",
            }
        self.assertLess(
            len(rs.image_base64_cache), 500,
            "image_base64_cache is unbounded",
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
        """Calling write_analysis N times for the same conversation
        must not grow analysis-history.yaml to N entries — it rotates
        at a documented cap."""
        from analyser import analyse

        # Seed with an initial analysis so each subsequent call snapshots.
        (self.conv_dir / "analysis.yaml").write_text(
            yaml.dump(self._sample_analysis("v0"), sort_keys=False),
            encoding="utf-8",
        )

        # 50 re-analyses
        for i in range(1, 51):
            analyse.write_analysis(self.conv_dir, self._sample_analysis(f"v{i}"))

        history_path = self.conv_dir / "analysis-history.yaml"
        history = yaml.safe_load(history_path.read_text(encoding="utf-8"))
        self.assertLess(
            len(history), 50,
            f"analysis-history.yaml has {len(history)} entries — "
            "rotation cap not enforced",
        )


if __name__ == "__main__":
    unittest.main()
