"""Tests for per-conversation context snapshot written by RequestLogger.

The snapshot captures non-secret coordinator config at the time of the
conversation (persona, default zone, coordinator model + profile tuning,
registered skill names/descriptions) so the passive analyser reads
at-the-time state instead of live env at analysis time.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.runtime.request_logger import RequestLogger


class TestWriteContextSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_logger(self, request_id="rq-c01-0001"):
        return RequestLogger(request_id, logs_root=self.tmp)

    def _snapshot_path(self, logger):
        return logger.request_dir.parent / "context_snapshot.json"

    def _sample_snapshot(self, **overrides):
        data = {
            "persona": "Peter Griffin",
            "default_zone": "Kitchen",
            "coordinator_model": "anthropic/claude-sonnet-4-6",
            "model_profile": {
                "max_coordinator_steps": 12,
                "temperature": 0.0,
                "top_p": None,
            },
            "registered_skills": [
                {"name": "roon_search", "description": "Browse and search Roon."},
                {"name": "roon_action", "description": "Transport controls."},
            ],
        }
        data.update(overrides)
        return data

    def test_snapshot_written_on_first_request(self):
        logger = self._make_logger("rq-c01-0001")
        written = logger.write_context_snapshot(self._sample_snapshot())
        assert written is True
        path = self._snapshot_path(logger)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["persona"] == "Peter Griffin"
        assert data["default_zone"] == "Kitchen"
        assert data["coordinator_model"] == "anthropic/claude-sonnet-4-6"
        assert data["model_profile"]["max_coordinator_steps"] == 12
        assert [s["name"] for s in data["registered_skills"]] == ["roon_search", "roon_action"]
        assert "timestamp" in data

    def test_snapshot_not_overwritten_on_subsequent_request(self):
        logger1 = self._make_logger("rq-c01-0001")
        logger1.write_context_snapshot(self._sample_snapshot(persona="First"))

        logger2 = self._make_logger("rq-c01-0002")
        # Same conversation — subsequent request should not overwrite.
        written = logger2.write_context_snapshot(self._sample_snapshot(persona="Second"))
        assert written is False

        path = self._snapshot_path(logger2)
        data = json.loads(path.read_text())
        assert data["persona"] == "First"

    def test_snapshot_separate_per_conversation(self):
        logger_c01 = self._make_logger("rq-c01-0001")
        logger_c01.write_context_snapshot(self._sample_snapshot(persona="C01"))

        logger_c02 = self._make_logger("rq-c02-0001")
        written = logger_c02.write_context_snapshot(self._sample_snapshot(persona="C02"))
        assert written is True

        c01 = json.loads(self._snapshot_path(logger_c01).read_text())
        c02 = json.loads(self._snapshot_path(logger_c02).read_text())
        assert c01["persona"] == "C01"
        assert c02["persona"] == "C02"

    def test_snapshot_with_none_fields_omitted(self):
        """Unset env vars (no persona, no zone) should still produce a valid snapshot."""
        snapshot = {
            "persona": None,
            "default_zone": None,
            "coordinator_model": "ollama_chat/gemma4:26b",
            "model_profile": {"max_coordinator_steps": 12, "temperature": 0.7, "top_p": None},
            "registered_skills": [],
        }
        logger = self._make_logger()
        logger.write_context_snapshot(snapshot)
        data = json.loads(self._snapshot_path(logger).read_text())
        assert data["persona"] is None
        assert data["default_zone"] is None
        assert data["registered_skills"] == []


if __name__ == "__main__":
    unittest.main()
