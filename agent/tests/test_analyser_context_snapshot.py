"""Tests for the passive analyser's context-snapshot reading.

The analyser reads per-conversation context_snapshot.json (captured by
the agent at conversation start) so findings reflect at-the-time state
rather than live env / model_profiles at analysis time.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analyser import analyse  # noqa: E402


class TestLoadContextSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.conv_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_snapshot_returns_none(self):
        assert analyse._load_context_snapshot(self.conv_dir) is None

    def test_malformed_snapshot_returns_none(self):
        (self.conv_dir / "context_snapshot.json").write_text("{not json")
        assert analyse._load_context_snapshot(self.conv_dir) is None

    def test_valid_snapshot_parsed(self):
        payload = {"persona": "P", "registered_skills": []}
        (self.conv_dir / "context_snapshot.json").write_text(json.dumps(payload))
        loaded = analyse._load_context_snapshot(self.conv_dir)
        assert loaded == payload


class TestFormatCoordinatorConfigBlock(unittest.TestCase):
    def test_full_snapshot_renders_all_fields(self):
        snapshot = {
            "persona": "Peter Griffin",
            "default_zone": "Kitchen",
            "coordinator_model": "anthropic/claude-sonnet-4-6",
            "model_profile": {
                "max_coordinator_steps": 14,
                "temperature": 0.2,
                "top_p": None,
            },
            "registered_skills": [
                {"name": "roon_search", "description": "Browse Roon library."},
                {"name": "web_search", "description": "Search the public web."},
            ],
        }
        block = analyse._format_coordinator_config_block(snapshot)
        assert block is not None
        assert "## Coordinator configuration" in block
        assert "Persona: Peter Griffin" in block
        assert "Default Roon zone: Kitchen" in block
        assert "Coordinator model: anthropic/claude-sonnet-4-6" in block
        assert "Max coordinator steps: 14" in block
        assert "Temperature: 0.2" in block
        assert "roon_search — Browse Roon library." in block
        assert "web_search — Search the public web." in block

    def test_web_search_absent_when_unregistered(self):
        """SEARXNG_URL unset, web_search not registered."""
        snapshot = {
            "persona": None,
            "default_zone": None,
            "coordinator_model": "anthropic/claude-sonnet-4-6",
            "model_profile": {"max_coordinator_steps": 12, "temperature": 0.0},
            "registered_skills": [
                {"name": "roon_search", "description": "Browse Roon library."},
            ],
        }
        block = analyse._format_coordinator_config_block(snapshot)
        assert block is not None
        assert "roon_search" in block
        assert "web_search" not in block

    def test_empty_snapshot_returns_none(self):
        assert analyse._format_coordinator_config_block({}) is None

    def test_description_newlines_flattened(self):
        snapshot = {
            "registered_skills": [
                {"name": "roon_search", "description": "Line one.\nLine two."},
            ],
        }
        block = analyse._format_coordinator_config_block(snapshot)
        assert block is not None
        assert "Line one. Line two." in block
        assert "\n    - roon_search" in block  # skill on own line, desc flat


class TestBuildCoordinatorConfigBlock(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.conv_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_block_when_snapshot_present(self):
        snapshot = {
            "persona": "From snapshot",
            "registered_skills": [],
        }
        (self.conv_dir / "context_snapshot.json").write_text(json.dumps(snapshot))
        block = analyse.build_coordinator_config_block(self.conv_dir)
        assert block is not None
        assert "From snapshot" in block

    def test_returns_none_when_snapshot_absent(self):
        assert analyse.build_coordinator_config_block(self.conv_dir) is None


if __name__ == "__main__":
    unittest.main()
