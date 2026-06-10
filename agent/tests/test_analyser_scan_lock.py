"""Tests for the passive analyser's scan lock.

The lock serialises scan cycles across processes so the scheduled
passive scan and an on-demand scan invoked from the agent never
double-process the same unanalysed conversations. Backed by
``filelock.FileLock`` which is cross-platform (flock on POSIX,
msvcrt.locking on Windows).
"""

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from filelock import FileLock

from analyser import analyse  # noqa: E402


class TestAcquireScanLock(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "scan.lock"

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquires_when_free(self):
        with analyse.acquire_scan_lock(self.lock_path) as acquired:
            assert acquired is True

    def test_second_holder_fails_when_first_active(self):
        # Simulate another process holding the lock by taking it ourselves
        # via a separate FileLock instance on the same path.
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = FileLock(str(self.lock_path), timeout=0)
        holder.acquire()
        try:
            with analyse.acquire_scan_lock(self.lock_path) as acquired:
                assert acquired is False
        finally:
            holder.release()

    def test_releases_so_next_caller_can_acquire(self):
        with analyse.acquire_scan_lock(self.lock_path) as first:
            assert first is True
        # After exit, a separate holder should be free to take it.
        holder = FileLock(str(self.lock_path), timeout=0)
        holder.acquire()
        holder.release()

    def test_creates_parent_dir(self):
        deep_path = Path(self._tmp.name) / "deep" / "nested" / "scan.lock"
        with analyse.acquire_scan_lock(deep_path) as acquired:
            assert acquired is True
        assert deep_path.parent.is_dir()

    def test_yields_false_when_parent_mkdir_fails(self):
        """If the lock dir can't be created (permissions, full disk),
        acquire_scan_lock must yield False and log an error rather than
        crashing the scan cycle."""
        def boom(*_args, **_kwargs):
            raise PermissionError("denied")

        with patch.object(Path, "mkdir", boom):
            with self.assertLogs("analyse", level=logging.ERROR) as captured:
                with analyse.acquire_scan_lock(self.lock_path) as acquired:
                    assert acquired is False
        # Confirm we logged the cause, not just that *something* went wrong
        self.assertTrue(
            any("scan lock directory" in rec.getMessage() for rec in captured.records),
            f"Expected an error log about the lock directory, got: {[r.getMessage() for r in captured.records]}",
        )


if __name__ == "__main__":
    unittest.main()
