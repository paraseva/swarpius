"""Tests for _match_parsed_analyses in analyse.py.

The helper always matches by (conversation_id, date), never by
positional order — a reordered-but-complete LLM response must not
silently write analyses into the wrong conversation directories.
Unidentified or extra items are dropped; missing ones yield None at
that position.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analyser.analyse import _match_parsed_analyses  # noqa: E402


def _conv_dirs_for(base: Path, pairs: list[tuple[str, str]]) -> list[Path]:
    """Make date/cNN subdirs under base; return the Path list."""
    out: list[Path] = []
    for date, cid in pairs:
        d = base / date / cid
        d.mkdir(parents=True)
        out.append(d)
    return out


def _analysis(cid: str, date: str, **extra) -> dict:
    entry = {"conversation_id": cid, "date": date, "topic": f"topic for {cid}"}
    entry.update(extra)
    return entry


class TestMatchParsedAnalyses(unittest.TestCase):

    def test_batch_reordered_by_llm_matches_on_cid_and_date(self) -> None:
        """The core regression: LLM returns correct count but in a
        different order. Before the fix, positional trust wrote each
        analysis into the wrong conversation dir.
        """
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [
                ("2026-04-17", "c01"),
                ("2026-04-17", "c02"),
                ("2026-04-17", "c03"),
            ])
            parsed = [
                _analysis("c03", "2026-04-17", topic="third conv"),
                _analysis("c01", "2026-04-17", topic="first conv"),
                _analysis("c02", "2026-04-17", topic="second conv"),
            ]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["topic"], "first conv")
            self.assertEqual(result[1]["topic"], "second conv")
            self.assertEqual(result[2]["topic"], "third conv")

    def test_batch_correct_order_still_works(self) -> None:
        """Happy path: order already matches; should round-trip."""
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [
                ("2026-04-17", "c01"),
                ("2026-04-17", "c02"),
            ])
            parsed = [
                _analysis("c01", "2026-04-17"),
                _analysis("c02", "2026-04-17"),
            ]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["conversation_id"], "c01")
            self.assertEqual(result[1]["conversation_id"], "c02")

    def test_batch_missing_items_yield_none(self) -> None:
        """LLM returns fewer items than requested; unmatched positions
        are None, matched ones carry the right analysis.
        """
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [
                ("2026-04-17", "c01"),
                ("2026-04-17", "c02"),
                ("2026-04-17", "c03"),
            ])
            parsed = [_analysis("c02", "2026-04-17")]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertIsNone(result[0])
            self.assertEqual(result[1]["conversation_id"], "c02")
            self.assertIsNone(result[2])

    def test_batch_extra_items_are_silently_dropped(self) -> None:
        """LLM returns items we didn't ask for (stray cid). They go
        nowhere and don't displace legitimate items.
        """
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [
                ("2026-04-17", "c01"),
            ])
            parsed = [
                _analysis("c01", "2026-04-17"),
                _analysis("c99", "2026-04-17"),  # not in conv_dirs
            ]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["conversation_id"], "c01")

    def test_batch_items_missing_cid_are_dropped(self) -> None:
        """A dict without conversation_id can't be paired — don't
        guess. Caller sees None at that position.
        """
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [("2026-04-17", "c01")])
            parsed = [{"topic": "no cid", "date": "2026-04-17"}]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertIsNone(result[0])

    def test_batch_non_dict_items_are_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [("2026-04-17", "c01")])
            parsed = ["not a dict", _analysis("c01", "2026-04-17")]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["conversation_id"], "c01")

    def test_duplicate_cid_in_response_does_not_double_assign(self) -> None:
        """If the LLM emits the same cid twice, the second copy must
        not overwrite the first (or land in a different slot).
        """
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [
                ("2026-04-17", "c01"),
                ("2026-04-17", "c02"),
            ])
            parsed = [
                _analysis("c01", "2026-04-17", topic="first"),
                _analysis("c01", "2026-04-17", topic="duplicate"),
            ]
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["topic"], "first")
            self.assertIsNone(result[1])

    def test_single_conversation_accepts_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [("2026-04-17", "c01")])
            parsed = _analysis("c01", "2026-04-17")
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["conversation_id"], "c01")

    def test_batch_got_single_dict_salvages_identifiable_item(self) -> None:
        """Caller asked for a batch but got a bare object. Treat the
        object as a one-item list: if it identifies one of our
        conv_dirs, place it there; the rest stay None.
        """
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [
                ("2026-04-17", "c01"),
                ("2026-04-17", "c02"),
            ])
            parsed = _analysis("c01", "2026-04-17")
            result = _match_parsed_analyses(parsed, conv_dirs)
            self.assertEqual(result[0]["conversation_id"], "c01")
            self.assertIsNone(result[1])

    def test_non_list_non_dict_response_is_all_none(self) -> None:
        """A string or None top-level response is unrecoverable."""
        with TemporaryDirectory() as tmp:
            conv_dirs = _conv_dirs_for(Path(tmp), [("2026-04-17", "c01")])
            self.assertEqual(
                _match_parsed_analyses("not json", conv_dirs),
                [None],
            )


if __name__ == "__main__":
    unittest.main()
