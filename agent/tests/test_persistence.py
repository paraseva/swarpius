"""Tests for PersistenceManager — save/restore over registered participants.

These exercise the real manager against a real temp SQLite DB. The
participants are minimal real objects implementing the PersistentState
protocol (not mocks that echo a canned answer): a value is set, saved via
the manager, then restored into a *fresh* participant through a *fresh*
manager reading the same DB, and asserted to survive — so the manager's
serialise → write → read → restore path is genuinely exercised.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.io.state_db import StateDb
from app.runtime.persistence import PersistenceManager


class _Zone:
    """A minimal real participant: holds one value, captured/restored as a dict."""

    state_key = "test_zone"

    def __init__(self, name=None):
        self.name = name

    def capture_state(self):
        return {"name": self.name}

    def restore_state(self, data):
        self.name = data.get("name")


class _Exploding:
    state_key = "boom"

    def capture_state(self):
        raise RuntimeError("capture failed")

    def restore_state(self, data):
        pass


class TestPersistenceManager(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_state_survives_save_and_reload(self):
        # Original process: register a participant with state, commit.
        manager = PersistenceManager(self.db)
        manager.register(_Zone(name="Kitchen"))
        manager.commit()

        # Fresh process: a new manager reads the saved bag; a fresh
        # participant restores its slice.
        reborn = PersistenceManager(self.db)
        restored = _Zone()
        slice_ = reborn.restored_slice(restored.state_key)
        self.assertIsNotNone(slice_)
        restored.restore_state(slice_)
        self.assertEqual(restored.name, "Kitchen")

    def test_commit_upserts_latest_state(self):
        zone = _Zone(name="Kitchen")
        manager = PersistenceManager(self.db)
        manager.register(zone)
        manager.commit()
        zone.name = "Bedroom"
        manager.commit()

        reborn = PersistenceManager(self.db)
        self.assertEqual(reborn.restored_slice("test_zone"), {"name": "Bedroom"})

    def test_restored_slice_unknown_key_returns_none(self):
        manager = PersistenceManager(self.db)
        self.assertIsNone(manager.restored_slice("never_saved"))

    def test_commit_is_atomic_across_participants(self):
        """If one participant's capture raises, the whole commit rolls back —
        no participant's state is left half-written."""
        manager = PersistenceManager(self.db)
        manager.register(_Zone(name="Kitchen"))
        manager.register(_Exploding())
        with self.assertRaises(RuntimeError):
            manager.commit()

        reborn = PersistenceManager(self.db)
        self.assertIsNone(reborn.restored_slice("test_zone"))


if __name__ == "__main__":
    unittest.main()
