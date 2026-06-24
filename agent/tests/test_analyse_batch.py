"""analyse_batch orchestration — only the LLM boundary (llm_completion) is
patched.

Contract (the batch-analysis design): a valid JSON batch response is parsed
and matched back to each conversation by (conversation_id, date), never by
position; an LLM failure (text None) and an unparseable response each yield
one None per conversation, so no conversation is silently mis-analysed.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analyser import analyse as analyse_mod  # noqa: E402
from analyser.analyse import analyse_batch  # noqa: E402
from analyser.llm_layer import CompletionResult  # noqa: E402


def _conv_dirs(base: Path, pairs: list[tuple[str, str]]) -> list[Path]:
    out: list[Path] = []
    for date, cid in pairs:
        d = base / date / cid
        d.mkdir(parents=True)
        out.append(d)
    return out


def _analysis(cid: str, date: str) -> dict:
    return {"conversation_id": cid, "date": date, "topic": f"topic {cid}"}


class TestAnalyseBatchOrchestration(unittest.TestCase):
    def test_valid_response_matched_to_each_conversation(self):
        with TemporaryDirectory() as tmp:
            dirs = _conv_dirs(Path(tmp), [
                ("2026-04-17", "c01"), ("2026-04-17", "c02")])
            # LLM returns the two analyses in the OPPOSITE order.
            payload = json.dumps([
                _analysis("c02", "2026-04-17"),
                _analysis("c01", "2026-04-17"),
            ])
            with patch.object(analyse_mod, "llm_completion",
                              return_value=CompletionResult(text=payload)):
                result = analyse_batch("anthropic/x", "key", dirs, "guide", None)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["conversation_id"], "c01")
            self.assertEqual(result[1]["conversation_id"], "c02")

    def test_permanent_llm_failure_halts(self):
        """A permanent error (auth/misconfig) would fail every conversation
        in the batch identically, so analyse_batch halts loudly rather than
        silently nulling them all out."""
        with TemporaryDirectory() as tmp:
            dirs = _conv_dirs(Path(tmp), [
                ("2026-04-17", "c01"), ("2026-04-17", "c02")])
            with patch.object(
                analyse_mod, "llm_completion",
                return_value=CompletionResult(text=None, error_kind="permanent"),
            ):
                with self.assertRaises(analyse_mod.AnalyserFatalError):
                    analyse_batch("anthropic/x", "key", dirs, "guide", None)

    def test_unparseable_response_yields_none_per_conversation(self):
        with TemporaryDirectory() as tmp:
            dirs = _conv_dirs(Path(tmp), [("2026-04-17", "c01")])
            with patch.object(analyse_mod, "llm_completion",
                              return_value=CompletionResult(text="not json at all")):
                result = analyse_batch("anthropic/x", "key", dirs, "guide", None)
            self.assertEqual(result, [None])

    def test_input_shape_failure_marks_single_conversation_skipped(self):
        """A conversation too large to analyse is marked skipped (so it is not
        re-attempted every scan), not silently nulled and retried forever."""
        with TemporaryDirectory() as tmp:
            dirs = _conv_dirs(Path(tmp), [("2026-04-17", "c01")])
            with patch.object(
                analyse_mod, "llm_completion",
                return_value=CompletionResult(
                    text=None, error_kind="input_shape", detail="ContextLengthExceeded"),
            ):
                result = analyse_batch("anthropic/x", "key", dirs, "guide", None)
            self.assertEqual(result, [None])
            self.assertTrue((dirs[0] / "analysis.skipped.yaml").exists())

    def test_transient_failure_leaves_conversation_unmarked(self):
        """A transient failure may succeed on a later scan, so it must NOT be
        marked skipped — it stays eligible."""
        with TemporaryDirectory() as tmp:
            dirs = _conv_dirs(Path(tmp), [("2026-04-17", "c01")])
            with patch.object(
                analyse_mod, "llm_completion",
                return_value=CompletionResult(
                    text=None, error_kind="transient", detail="429 rate limit"),
            ):
                result = analyse_batch("anthropic/x", "key", dirs, "guide", None)
            self.assertEqual(result, [None])
            self.assertFalse((dirs[0] / "analysis.skipped.yaml").exists())


if __name__ == "__main__":
    unittest.main()
