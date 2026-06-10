"""``IMAGE_CACHE_MAX_ENTRIES`` must flow through to the live
artwork cache after ``ensure_initialised`` runs.

The existing ``test_result_store_bounds.py`` verifies that the
bounded-dict cap is enforced once set, but constructs the caches
directly with the default cap — it never exercises the
env-var-to-cache-cap path. Without this test the
``_apply_settings_capacity_overrides`` override branch is
behaviourally uncovered.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _env_and_settings_cache(env: dict):
    """Push env vars + drop the settings cache so the locked
    snapshot reflects them."""
    from app.settings.core import reset_settings_for_tests
    with patch.dict(os.environ, env, clear=False):
        reset_settings_for_tests()
        try:
            yield
        finally:
            reset_settings_for_tests()


def _initialise_runtime():
    """Run RuntimeState through ensure_initialised with the Roon
    connection + skill loaders stubbed (we care about artwork cap,
    not Roon)."""
    from app.runtime.state import RuntimeState
    rs = RuntimeState()
    with (
        patch("app.runtime.state.RoonConnection", MagicMock),
        patch("app.runtime.state._load_agent_skills", return_value=[]),
        patch(
            "app.runtime.state._format_agent_skills_for_prompt",
            return_value=("", ""),
        ),
    ):
        rs.ensure_initialised()
    return rs


class TestArtworkCacheCapFromSettings(unittest.TestCase):
    """The artwork cache's eviction cap must equal whatever Settings
    resolves from ``IMAGE_CACHE_MAX_ENTRIES`` (default 200)."""

    def _fill_and_assert_cap(self, cap: int):
        rs = _initialise_runtime()
        for i in range(cap * 2):
            rs.image_base64_cache[f"key-{i}"] = b"x"
        # The cache must have evicted down to (at most) the cap.
        self.assertLessEqual(
            len(rs.image_base64_cache), cap,
            f"image_base64_cache has {len(rs.image_base64_cache)} entries, "
            f"expected ≤ {cap}",
        )

    def test_default_cap_is_observed(self):
        env = {
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "k",
            "DEFAULT_ROON_ZONE": "Living Room",
        }
        with _env_and_settings_cache(env):
            self._fill_and_assert_cap(200)

    def test_overridden_cap_takes_effect_end_to_end(self):
        """Sets IMAGE_CACHE_MAX_ENTRIES=25 and verifies the actual
        artwork cache enforces the 25-entry cap."""
        env = {
            "LLM_MODEL": "dummy/dummy-model",
            "LLM_API_KEY_DUMMY": "k",
            "DEFAULT_ROON_ZONE": "Living Room",
            "IMAGE_CACHE_MAX_ENTRIES": "25",
        }
        with _env_and_settings_cache(env):
            self._fill_and_assert_cap(25)


if __name__ == "__main__":
    unittest.main()
