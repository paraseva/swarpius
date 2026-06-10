"""Tests for ``app/runtime/server_logger.ServerLogger``.

The server logger writes one YAML file per request directory, with
``---``-separated documents per ``log()`` call. Coverage was 41% per
phase-3d-gaps.md — only the cleanup path and request-id parsing were
exercised indirectly. This file covers the writer end-to-end against
a tempdir.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import yaml

from app.runtime import server_logger as srv
from app.runtime.server_logger import (
    NullServerLogger,
    ServerLogger,
    cleanup_old_server_logs,
)


class TestExtractConvDir(unittest.TestCase):
    """Pure helper — request_id parsing."""

    def test_valid_request_id_returns_conv_id(self):
        self.assertEqual(
            ServerLogger._extract_conv_dir("rq-c01-0042"), "c01",
        )

    def test_none_returns_none(self):
        self.assertIsNone(ServerLogger._extract_conv_dir(None))

    def test_missing_rq_prefix_returns_none(self):
        self.assertIsNone(ServerLogger._extract_conv_dir("c01-0042"))

    def test_too_few_parts_returns_none(self):
        self.assertIsNone(ServerLogger._extract_conv_dir("rq-c01"))


class TestLog(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.logger = ServerLogger(root=self.root)
        self.today = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self) -> None:
        self.logger.close()
        self._tmp.cleanup()

    def _read_yaml_docs(self, request_id: str) -> list:
        path = (
            self.root / self.today
            / request_id.split("-")[1] / request_id / "server.yaml"
        )
        text = path.read_text(encoding="utf-8")
        return [d for d in yaml.safe_load_all(text) if d is not None]

    def test_log_without_request_id_is_silent(self):
        """No request_id set → write should silently skip (don't crash,
        don't create stray files)."""
        self.logger.log("op_x", detail="anything")
        self.assertEqual(list(self.root.rglob("server.yaml")), [])

    def test_log_writes_entry_with_op_request_id_timestamp(self):
        self.logger.set_request_id("rq-c01-0001")
        self.logger.log("resolve_ref", ref_id="00007", tier="key_live")

        docs = self._read_yaml_docs("rq-c01-0001")
        self.assertEqual(len(docs), 1)
        entry = docs[0]
        self.assertEqual(entry["op"], "resolve_ref")
        self.assertEqual(entry["request_id"], "rq-c01-0001")
        self.assertEqual(entry["ref_id"], "00007")
        self.assertEqual(entry["tier"], "key_live")
        # Timestamp is an ISO-ish string with millisecond precision.
        self.assertIn("T", entry["ts"])

    def test_multiple_logs_same_request_id_append(self):
        self.logger.set_request_id("rq-c01-0002")
        self.logger.log("op_a", x=1)
        self.logger.log("op_b", y=2)
        self.logger.log("op_c", z=3)

        docs = self._read_yaml_docs("rq-c01-0002")
        self.assertEqual([d["op"] for d in docs], ["op_a", "op_b", "op_c"])

    def test_request_id_change_switches_files(self):
        self.logger.set_request_id("rq-c01-0001")
        self.logger.log("op_a")
        self.logger.set_request_id("rq-c02-0001")
        self.logger.log("op_b")

        first = self._read_yaml_docs("rq-c01-0001")
        second = self._read_yaml_docs("rq-c02-0001")
        self.assertEqual([d["op"] for d in first], ["op_a"])
        self.assertEqual([d["op"] for d in second], ["op_b"])
        # Two distinct conversation directories, not one shared file.
        self.assertNotEqual(first[0]["request_id"], second[0]["request_id"])

    def test_log_stringifies_non_serialisable_values(self):
        """An arbitrary object passed via kwargs becomes a string in
        the dumped YAML — protects against accidentally killing the
        logger when production code passes something exotic."""
        class _Obj:
            def __str__(self) -> str:
                return "from-str"

        self.logger.set_request_id("rq-c01-0003")
        self.logger.log("op", payload=_Obj())

        docs = self._read_yaml_docs("rq-c01-0003")
        self.assertEqual(docs[0]["payload"], "from-str")

    def test_log_swallows_internal_errors(self):
        """If something below the public log() boundary raises, the
        caller mustn't see it — server logging is best-effort."""
        broken = ServerLogger(root=self.root)
        broken.set_request_id("rq-c01-0099")
        # Forcing yaml.dump to fail by patching it on the instance's
        # yaml module reference isn't worth the gymnastics — instead
        # we pass an unhashable kwarg that yaml itself rejects after
        # _stringify converts it (an open file handle stringifies fine).
        # Simulate by closing the active file out from under the logger
        # AFTER its first write, then logging again.
        broken.log("first", detail="ok")
        # Force a stale path by reassigning to an immutable string —
        # the next mkdir will raise; log() should swallow.
        broken._current_path = "/nonexistent/no-write"  # type: ignore[assignment]
        broken._current_fh = None
        broken._root = Path("/proc/cannot-write-here")  # type: ignore[assignment]
        broken.log("second", detail="should-be-swallowed")  # no raise
        broken.close()


class TestNullServerLogger(unittest.TestCase):

    def test_null_logger_silently_accepts_all_methods(self):
        null = NullServerLogger()
        null.set_request_id("rq-c01-0001")
        null.log("any_op", any_kw=1)
        null.close()
        # No assertion beyond "doesn't raise" — that's the whole contract.


class TestCleanupOldServerLogs(unittest.TestCase):
    """``cleanup_old_server_logs`` deletes date-named subdirectories
    older than the retention window."""

    def test_returns_zero_when_root_missing(self):
        """Patching _LOG_ROOT to a nonexistent path makes the function
        report zero removals rather than raising."""
        original = srv._LOG_ROOT
        srv._LOG_ROOT = Path("/nonexistent/server-logs-root")
        try:
            self.assertEqual(cleanup_old_server_logs(retention_days=7), 0)
        finally:
            srv._LOG_ROOT = original

    def test_old_directories_removed_recent_kept(self):
        original = srv._LOG_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            srv._LOG_ROOT = Path(tmp)
            (srv._LOG_ROOT / "2020-01-01").mkdir()
            (srv._LOG_ROOT / "2020-06-15").mkdir()
            today = datetime.now().strftime("%Y-%m-%d")
            (srv._LOG_ROOT / today).mkdir()
            # Sentinel non-date entry — left alone unless older than cutoff
            # (its name "z-not-a-date" sorts after any date and so is
            # treated as recent under name-based comparison).
            (srv._LOG_ROOT / "z-not-a-date").mkdir()
            try:
                removed = cleanup_old_server_logs(retention_days=7)
                self.assertEqual(removed, 2)
                self.assertFalse((srv._LOG_ROOT / "2020-01-01").exists())
                self.assertFalse((srv._LOG_ROOT / "2020-06-15").exists())
                self.assertTrue((srv._LOG_ROOT / today).exists())
                self.assertTrue((srv._LOG_ROOT / "z-not-a-date").exists())
            finally:
                srv._LOG_ROOT = original


if __name__ == "__main__":
    unittest.main()
