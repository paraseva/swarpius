"""Tests for execution trace compaction in build_trace_context.

Verifies that older roon_search entries are compacted to summary form
when current_global_step is provided, and that the compact format
correctly handles the trace entry format (items list, not groups).
"""

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from app.coordinator.trace import build_trace_context


def _at(ts_str: str) -> str:
    """Return an ISO-format timestamp string for a YYYY-MM-DD HH:MM literal."""
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M").isoformat(timespec="seconds")


def _search_entry(global_step: int, items: list[str], step: int = 1) -> dict:
    """Build a roon_search trace entry matching _step_trace output format."""
    return {
        "step": step,
        "global_step": global_step,
        "selected_skill": "roon_search",
        "tool_parameters": {
            "operation": "new_search",
            "search_string": f"query_{global_step}",
        },
        "tool_output": {
            "description": f"Search results for query_{global_step}.",
            "items": items,
        },
        "note": None,
    }


def _action_entry(global_step: int, step: int = 1) -> dict:
    """Build a non-search trace entry (should never be compacted)."""
    return {
        "step": step,
        "global_step": global_step,
        "selected_skill": "roon_action",
        "tool_parameters": {"action": "play"},
        "tool_output": {"status": "ok"},
        "note": None,
    }


class TestTraceCompaction(unittest.TestCase):

    def test_old_search_entries_are_compacted(self):
        """roon_search entries older than full_results_window get compacted."""
        items = ["(0) [abc12] Track A | Artist", "(1) [def34] Track B | Artist"]
        trace = [
            _search_entry(global_step=1, items=items),
            _search_entry(global_step=5, items=["(0) [ghi56] Track C | Artist"]),
        ]
        result = json.loads(build_trace_context(
            trace, current_global_step=6, full_results_window=1,
        ))
        # Entry at global_step=1 should be compacted (6 - 1 = 5 > 1)
        self.assertNotIn("items", result[0]["tool_output"])
        self.assertEqual(result[0]["tool_output"]["description"], "Search results for query_1.")
        self.assertEqual(result[0]["tool_output"]["total_items"], 2)

        # Entry at global_step=5 should keep full items (6 - 5 = 1, not > 1)
        self.assertIn("items", result[1]["tool_output"])
        self.assertEqual(len(result[1]["tool_output"]["items"]), 1)

    def test_non_search_entries_never_compacted(self):
        """roon_action and other entries are never compacted regardless of age."""
        trace = [
            _action_entry(global_step=1),
            _search_entry(global_step=5, items=["(0) [abc12] Track | Artist"]),
        ]
        result = json.loads(build_trace_context(
            trace, current_global_step=10, full_results_window=1,
        ))
        self.assertEqual(result[0]["tool_output"]["status"], "ok")

    def test_zero_global_step_skips_compaction(self):
        """When current_global_step=0 (default), no compaction occurs."""
        items = ["(0) [abc12] Track | Artist"]
        trace = [_search_entry(global_step=5, items=items)]
        result = json.loads(build_trace_context(trace))
        # 0 - 5 = -5, not > 1, so no compaction
        self.assertIn("items", result[0]["tool_output"])

    def test_empty_items_compacts_to_zero(self):
        """Compaction of a search with no items gives total_items=0."""
        trace = [_search_entry(global_step=1, items=[])]
        result = json.loads(build_trace_context(
            trace, current_global_step=10, full_results_window=1,
        ))
        self.assertEqual(result[0]["tool_output"]["total_items"], 0)

    def test_window_boundary_not_compacted(self):
        """Entry exactly at the window boundary is NOT compacted."""
        items = ["(0) [abc12] Track | Artist"]
        trace = [_search_entry(global_step=4, items=items)]
        # 5 - 4 = 1, which is NOT > 1 (must be strictly greater)
        result = json.loads(build_trace_context(
            trace, current_global_step=5, full_results_window=1,
        ))
        self.assertIn("items", result[0]["tool_output"])

    def test_window_boundary_plus_one_is_compacted(self):
        """Entry one step outside the window IS compacted."""
        items = ["(0) [abc12] Track | Artist"]
        trace = [_search_entry(global_step=3, items=items)]
        # 5 - 3 = 2 > 1, compacted
        result = json.loads(build_trace_context(
            trace, current_global_step=5, full_results_window=1,
        ))
        self.assertNotIn("items", result[0]["tool_output"])
        self.assertEqual(result[0]["tool_output"]["total_items"], 1)


class TestTraceCompactionAggressive(unittest.TestCase):
    """Aggressive mode (small-model profiles): summarises earlier steps
    to one-liners and only keeps the latest step verbatim."""

    def test_single_entry_returns_full_trace(self):
        """With one entry, there's nothing to summarise — return the
        trace as-is (no summary/latest_step envelope)."""
        trace = [_search_entry(global_step=1, items=["(0) [abc12] X | Y"])]
        result = json.loads(
            build_trace_context(trace, aggressive=True),
        )
        # Same shape as non-aggressive single-entry — flat list.
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["selected_skill"], "roon_search")
        self.assertIn("items", result[0]["tool_output"])

    def test_multiple_entries_summarise_earlier_and_keep_latest(self):
        """Earlier steps become one-line ``step N: <skill>`` summaries;
        the most recent step is kept verbatim under ``latest_step``."""
        trace = [
            _search_entry(global_step=1, items=["a"], step=1),
            _action_entry(global_step=2, step=2),
            _search_entry(global_step=3, items=["b"], step=3),
        ]
        result = json.loads(
            build_trace_context(trace, aggressive=True),
        )
        self.assertEqual(result["summary"]["total_steps"], 3)
        # Earlier steps as one-liners
        self.assertEqual(
            result["summary"]["earlier_steps"],
            ["step 1: roon_search", "step 2: roon_action"],
        )
        # Latest step kept verbatim (search at step 3, with items)
        self.assertEqual(result["latest_step"]["step"], 3)
        self.assertIn("items", result["latest_step"]["tool_output"])

    def test_note_appended_truncated_at_120_chars(self):
        """Earlier-step notes are included on the summary line,
        truncated to 120 characters."""
        long_note = "x" * 200
        entry = _search_entry(global_step=1, items=[], step=1)
        entry["note"] = long_note
        trace = [entry, _search_entry(global_step=2, items=[], step=2)]

        result = json.loads(
            build_trace_context(trace, aggressive=True),
        )
        summary_line = result["summary"]["earlier_steps"][0]
        self.assertTrue(summary_line.startswith("step 1: roon_search — "))
        # 120-char body, prefixed with "step 1: roon_search — "
        body = summary_line.split(" — ", 1)[1]
        self.assertEqual(len(body), 120)
        self.assertEqual(body, "x" * 120)

    def test_only_last_four_earlier_steps_retained(self):
        """``earlier_steps`` is capped at the most recent four
        pre-latest steps to bound prompt growth on long traces."""
        trace = [
            _search_entry(global_step=i, items=[], step=i)
            for i in range(1, 8)  # steps 1..7
        ]
        result = json.loads(
            build_trace_context(trace, aggressive=True),
        )
        earlier = result["summary"]["earlier_steps"]
        # Latest is step 7; earlier_steps draws from steps 1..6 but
        # caps at the last 4 (3..6).
        self.assertEqual(len(earlier), 4)
        self.assertTrue(earlier[0].startswith("step 3:"))
        self.assertTrue(earlier[-1].startswith("step 6:"))


class TestTraceTimestamps(unittest.TestCase):
    """Each trace entry carries a timestamp captured at creation time
    in _step_trace; build_trace_context renders both the absolute
    timestamp and a relative ``age`` so the LLM can see how stale each
    entry is — without it, an action from hours earlier looks
    indistinguishable from one that just happened."""

    def test_age_field_added_to_each_entry(self):
        trace = [{
            "step": 1, "global_step": 5,
            "selected_skill": "roon_config",
            "timestamp": _at("2026-05-25 01:35"),
            "tool_parameters": {"action": "Set Default Zone"},
            "tool_output": {"result": "ok"},
            "note": None,
        }]
        with patch("app.coordinator.trace.local_now") as mock_now:
            mock_now.return_value = datetime(2026, 5, 25, 13, 35)  # 12h later
            result = json.loads(build_trace_context(trace))
        self.assertEqual(result[0]["age"], "12 hr ago")

    def test_absolute_timestamp_reformatted_to_readable_form(self):
        trace = [{
            "step": 1, "global_step": 5,
            "selected_skill": "roon_action",
            "timestamp": _at("2026-05-25 01:35"),
            "tool_parameters": {},
            "tool_output": {},
            "note": None,
        }]
        with patch("app.coordinator.trace.local_now") as mock_now:
            mock_now.return_value = datetime(2026, 5, 25, 14, 0)
            result = json.loads(build_trace_context(trace))
        self.assertEqual(result[0]["timestamp"], "2026-05-25 01:35")

    def test_recent_entry_renders_just_now(self):
        trace = [{
            "step": 1, "global_step": 5,
            "selected_skill": "roon_action",
            "timestamp": _at("2026-05-25 14:00"),
            "tool_parameters": {},
            "tool_output": {},
            "note": None,
        }]
        with patch("app.coordinator.trace.local_now") as mock_now:
            mock_now.return_value = datetime(2026, 5, 25, 14, 0, 30)
            result = json.loads(build_trace_context(trace))
        self.assertEqual(result[0]["age"], "just now")

    def test_missing_timestamp_skips_annotation(self):
        """Legacy entries without a timestamp field must not crash and
        must not gain an ``age`` field (no claim we can't substantiate)."""
        trace = [{
            "step": 1, "global_step": 5,
            "selected_skill": "roon_action",
            "tool_parameters": {},
            "tool_output": {},
            "note": None,
        }]
        result = json.loads(build_trace_context(trace))
        self.assertNotIn("age", result[0])
        self.assertNotIn("timestamp", result[0])

    def test_aggressive_mode_includes_age_on_latest_entry(self):
        """In aggressive mode the latest entry is preserved verbatim;
        its age annotation must come along so stale-vs-live signal
        survives the small-model summarisation."""
        trace = [
            {
                "step": 1, "global_step": 1, "selected_skill": "roon_action",
                "timestamp": _at("2026-05-25 01:35"),
                "tool_parameters": {}, "tool_output": {}, "note": None,
            },
            {
                "step": 2, "global_step": 2, "selected_skill": "roon_action",
                "timestamp": _at("2026-05-25 14:00"),
                "tool_parameters": {}, "tool_output": {}, "note": None,
            },
        ]
        with patch("app.coordinator.trace.local_now") as mock_now:
            mock_now.return_value = datetime(2026, 5, 25, 14, 0, 30)
            result = json.loads(build_trace_context(trace, aggressive=True))
        self.assertEqual(result["latest_step"]["age"], "just now")
