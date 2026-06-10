"""Tests for the analysis browser module."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app.analysis.browser import (
    _find_unanalysed_conversations,
    get_analysis_detail,
    get_metrics,
    get_request_logs,
    get_result_handle_data,
    list_analysed_conversations,
    list_conversation_requests,
    run_analysis,
    scan_and_analyse,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

VALID_ANALYSIS_YAML = """\
analysed_at: '2026-03-29T13:00:00.000Z'
git_ref: a737abf
conversation_id: c02
date: '2026-03-29'
topic: Playing remastered version of Lone Ranger by Quantum Jump
requests_analysed: 5
total_tool_calls: 4
total_steps: 9
avg_steps_per_request: 1.8
findings:
- request_id: rq-c02-0005
  failure_mode: FM-19
  failure_name: Conversation grouping inconsistency
  severity: low
  summary: |-
    General knowledge question grouped in a music playback conversation
  detail: |-
    rq-c02-0005 asks for today's date, which is unrelated to the prior music flow.
notes: |-
  Very clean conversation overall.
"""

CLEAN_ANALYSIS_YAML = """\
analysed_at: '2026-03-29T12:00:00.000Z'
git_ref: a737abf
conversation_id: c01
date: '2026-03-29'
topic: Greeting and zone status check
requests_analysed: 2
total_tool_calls: 1
total_steps: 3
avg_steps_per_request: 1.5
findings: []
notes: |-
  Simple greeting followed by a zone status check. No issues.
"""

OLDER_ANALYSIS_YAML = """\
analysed_at: '2026-03-28T18:00:00.000Z'
git_ref: 09385c0
conversation_id: c05
date: '2026-03-28'
topic: Searching for jazz albums
requests_analysed: 3
total_tool_calls: 2
total_steps: 6
avg_steps_per_request: 2.0
findings:
- request_id: rq-c05-0002
  failure_mode: FM-06
  failure_name: Premature answer
  severity: medium
  summary: |-
    Agent answered before completing the search.
  detail: |-
    The agent returned results after only browsing the first page.
notes: |-
  Search flow had one issue.
"""


def _create_log_tree(root: Path) -> None:
    """Create a realistic log directory with analysis.yaml files."""
    # 2026-03-29/c01 — clean analysis
    c01_dir = root / "2026-03-29" / "c01"
    c01_dir.mkdir(parents=True)
    (c01_dir / "analysis.yaml").write_text(CLEAN_ANALYSIS_YAML)
    (c01_dir / "rq-c01-0001").mkdir()
    (c01_dir / "rq-c01-0002").mkdir()

    # 2026-03-29/c02 — analysis with findings
    c02_dir = root / "2026-03-29" / "c02"
    c02_dir.mkdir(parents=True)
    (c02_dir / "analysis.yaml").write_text(VALID_ANALYSIS_YAML)
    (c02_dir / "rq-c02-0001").mkdir()

    # 2026-03-28/c05 — older analysis
    c05_dir = root / "2026-03-28" / "c05"
    c05_dir.mkdir(parents=True)
    (c05_dir / "analysis.yaml").write_text(OLDER_ANALYSIS_YAML)

    # 2026-03-28/c03 — conversation WITHOUT analysis (should be skipped)
    c03_dir = root / "2026-03-28" / "c03"
    c03_dir.mkdir(parents=True)
    (c03_dir / "rq-c03-0001").mkdir()

    # 2026-03-28/c04 — malformed YAML (should be skipped gracefully)
    c04_dir = root / "2026-03-28" / "c04"
    c04_dir.mkdir(parents=True)
    (c04_dir / "analysis.yaml").write_text("not: valid: yaml: [[[")


# ---------------------------------------------------------------------------
# Tests — list_analysed_conversations
# ---------------------------------------------------------------------------


class TestListAnalysedConversations(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)
        _create_log_tree(self.logs_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_only_conversations_with_valid_analysis(self) -> None:
        convs = list_analysed_conversations(self.logs_root)["conversations"]
        ids = [(r["date"], r["conversation_id"]) for r in convs]
        self.assertIn(("2026-03-29", "c02"), ids)
        self.assertIn(("2026-03-29", "c01"), ids)
        self.assertIn(("2026-03-28", "c05"), ids)
        # c03 has no analysis.yaml, c04 has malformed YAML
        self.assertNotIn(("2026-03-28", "c03"), ids)
        self.assertNotIn(("2026-03-28", "c04"), ids)

    def test_sorted_most_recent_first(self) -> None:
        convs = list_analysed_conversations(self.logs_root)["conversations"]
        # 2026-03-29 conversations should come before 2026-03-28
        dates = [r["date"] for r in convs]
        self.assertEqual(dates, sorted(dates, reverse=True))
        # Within same date, higher conversation ID first
        mar29 = [r for r in convs if r["date"] == "2026-03-29"]
        self.assertEqual(mar29[0]["conversation_id"], "c02")
        self.assertEqual(mar29[1]["conversation_id"], "c01")

    def test_includes_expected_metadata_fields(self) -> None:
        convs = list_analysed_conversations(self.logs_root)["conversations"]
        entry = next(r for r in convs if r["conversation_id"] == "c02")
        self.assertEqual(entry["date"], "2026-03-29")
        self.assertEqual(entry["topic"], "Playing remastered version of Lone Ranger by Quantum Jump")
        self.assertEqual(entry["requests_analysed"], 5)
        self.assertEqual(entry["total_steps"], 9)
        self.assertAlmostEqual(entry["avg_steps_per_request"], 1.8)
        self.assertEqual(entry["git_ref"], "a737abf")
        self.assertEqual(entry["finding_count"], 1)

    def test_finding_count_zero_for_clean_conversation(self) -> None:
        convs = list_analysed_conversations(self.logs_root)["conversations"]
        entry = next(r for r in convs if r["conversation_id"] == "c01")
        self.assertEqual(entry["finding_count"], 0)

    def test_severity_summary(self) -> None:
        convs = list_analysed_conversations(self.logs_root)["conversations"]
        # c02 has one low finding
        c02 = next(r for r in convs if r["conversation_id"] == "c02")
        self.assertEqual(c02["severity_summary"], {"low": 1})
        # c05 has one medium finding
        c05 = next(r for r in convs if r["conversation_id"] == "c05")
        self.assertEqual(c05["severity_summary"], {"medium": 1})
        # c01 has no findings
        c01 = next(r for r in convs if r["conversation_id"] == "c01")
        self.assertEqual(c01["severity_summary"], {})

    def test_nonexistent_logs_directory(self) -> None:
        result = list_analysed_conversations(Path("/nonexistent/path"))
        self.assertEqual(result["conversations"], [])

    def test_date_from_filters_older_dates(self) -> None:
        convs = list_analysed_conversations(self.logs_root, date_from="2026-03-29")["conversations"]
        dates = {r["date"] for r in convs}
        self.assertEqual(dates, {"2026-03-29"})

    def test_date_to_filters_newer_dates(self) -> None:
        convs = list_analysed_conversations(self.logs_root, date_to="2026-03-28")["conversations"]
        dates = {r["date"] for r in convs}
        self.assertEqual(dates, {"2026-03-28"})

    def test_date_range_inclusive(self) -> None:
        convs = list_analysed_conversations(
            self.logs_root, date_from="2026-03-28", date_to="2026-03-29"
        )["conversations"]
        dates = {r["date"] for r in convs}
        self.assertEqual(dates, {"2026-03-28", "2026-03-29"})

    def test_date_range_excludes_all(self) -> None:
        convs = list_analysed_conversations(
            self.logs_root, date_from="2026-04-01", date_to="2026-04-02"
        )["conversations"]
        self.assertEqual(convs, [])

# ---------------------------------------------------------------------------
# Tests — get_analysis_detail
# ---------------------------------------------------------------------------


class TestGetAnalysisDetail(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)
        _create_log_tree(self.logs_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_full_analysis(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-03-29", "c02")
        self.assertIsNotNone(result)
        self.assertEqual(result["conversation_id"], "c02")
        self.assertEqual(result["topic"], "Playing remastered version of Lone Ranger by Quantum Jump")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["failure_mode"], "FM-19")
        self.assertIn("Very clean conversation", result["notes"])

    def test_returns_none_for_missing_conversation(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-03-29", "c99")
        self.assertIsNone(result)

    def test_returns_none_for_missing_date(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-04-01", "c01")
        self.assertIsNone(result)

    def test_returns_none_for_malformed_yaml(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-03-28", "c04")
        self.assertIsNone(result)

    def test_returns_none_for_no_analysis_file(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-03-28", "c03")
        self.assertIsNone(result)

    def test_clean_conversation_has_empty_findings(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-03-29", "c01")
        self.assertIsNotNone(result)
        self.assertEqual(result["findings"], [])

    def test_returns_empty_history_when_no_history_file(self) -> None:
        result = get_analysis_detail(self.logs_root, "2026-03-29", "c02")
        self.assertIsNotNone(result)
        self.assertEqual(result["history"], [])

    def test_returns_history_when_history_file_exists(self) -> None:
        conv_dir = self.logs_root / "2026-03-29" / "c02"
        history = [
            {
                "analysed_at": "2026-03-29T10:00:00Z",
                "git_ref": "abc1234",
                "superseded_at": "2026-03-29T13:00:00Z",
                "conversation_id": "c02",
                "date": "2026-03-29",
                "topic": "Playing remastered version of Lone Ranger by Quantum Jump",
                "findings": [
                    {
                        "request_id": "rq-c02-0003",
                        "failure_mode": "FM-08",
                        "severity": "medium",
                        "summary": "Old finding that was later resolved",
                    }
                ],
                "feedback": [],
                "notes": "Prior analysis notes.",
            }
        ]
        (conv_dir / "analysis-history.yaml").write_text(
            yaml.dump(history, default_flow_style=False)
        )
        result = get_analysis_detail(self.logs_root, "2026-03-29", "c02")
        self.assertIsNotNone(result)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["analysed_at"], "2026-03-29T10:00:00Z")
        self.assertEqual(result["history"][0]["findings"][0]["failure_mode"], "FM-08")
        self.assertEqual(result["history"][0]["feedback"], [])

    def test_returns_empty_history_for_corrupt_history_file(self) -> None:
        conv_dir = self.logs_root / "2026-03-29" / "c02"
        (conv_dir / "analysis-history.yaml").write_text("not: valid: yaml: [[[")
        result = get_analysis_detail(self.logs_root, "2026-03-29", "c02")
        self.assertIsNotNone(result)
        self.assertEqual(result["history"], [])


# ---------------------------------------------------------------------------
# Tests — get_metrics
# ---------------------------------------------------------------------------


class TestGetMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.metrics_path = self.tmp_root / "metrics.jsonl"
        lines = [
            {
                "analysed_at": "2026-03-28T10:00:00.000Z",
                "git_ref": "09385c0",
                "conversation_id": "c05",
                "date": "2026-03-28",
                "requests": 3,
                "steps": 6,
                "avg_steps": 2.0,
                "findings_by_mode": {"FM-06": 1},
                "findings_by_severity": {"medium": 1},
                "finding_count": 1,
            },
            {
                "analysed_at": "2026-03-29T12:00:00.000Z",
                "git_ref": "a737abf",
                "conversation_id": "c01",
                "date": "2026-03-29",
                "requests": 2,
                "steps": 3,
                "avg_steps": 1.5,
                "findings_by_mode": {},
                "findings_by_severity": {},
                "finding_count": 0,
            },
            {
                "analysed_at": "2026-03-29T13:00:00.000Z",
                "git_ref": "a737abf",
                "conversation_id": "c02",
                "date": "2026-03-29",
                "requests": 5,
                "steps": 9,
                "avg_steps": 1.8,
                "findings_by_mode": {"FM-19": 1},
                "findings_by_severity": {"low": 1},
                "finding_count": 1,
            },
        ]
        with self.metrics_path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_all_metrics_unfiltered(self) -> None:
        result = get_metrics(self.metrics_path)
        self.assertEqual(result["total_conversations"], 3)
        self.assertEqual(result["total_findings"], 2)

    def test_findings_by_severity_aggregated(self) -> None:
        result = get_metrics(self.metrics_path)
        self.assertEqual(result["findings_by_severity"]["low"], 1)
        self.assertEqual(result["findings_by_severity"]["medium"], 1)

    def test_findings_by_mode_aggregated(self) -> None:
        result = get_metrics(self.metrics_path)
        self.assertEqual(result["findings_by_mode"]["FM-06"], 1)
        self.assertEqual(result["findings_by_mode"]["FM-19"], 1)

    def test_avg_steps_computed(self) -> None:
        result = get_metrics(self.metrics_path)
        # (2.0 + 1.5 + 1.8) / 3 = 1.7666...
        self.assertAlmostEqual(result["avg_steps_per_request"], 1.77, places=2)

    def test_filter_by_after_date(self) -> None:
        result = get_metrics(self.metrics_path, after="2026-03-29")
        self.assertEqual(result["total_conversations"], 2)
        ids = {e["conversation_id"] for e in result["entries"]}
        self.assertEqual(ids, {"c01", "c02"})

    def test_filter_by_before_date(self) -> None:
        result = get_metrics(self.metrics_path, before="2026-03-28")
        self.assertEqual(result["total_conversations"], 1)

    def test_filter_by_ref(self) -> None:
        result = get_metrics(self.metrics_path, ref="a737abf")
        self.assertEqual(result["total_conversations"], 2)

    def test_ref_prefix_match(self) -> None:
        result = get_metrics(self.metrics_path, ref="a737")
        self.assertEqual(result["total_conversations"], 2)

    def test_empty_metrics_file(self) -> None:
        empty_path = self.tmp_root / "empty.jsonl"
        empty_path.write_text("")
        result = get_metrics(empty_path)
        self.assertEqual(result["total_conversations"], 0)
        self.assertEqual(result["total_findings"], 0)

    def test_nonexistent_metrics_file(self) -> None:
        result = get_metrics(Path("/nonexistent/metrics.jsonl"))
        self.assertEqual(result["total_conversations"], 0)

    def test_git_refs_listed(self) -> None:
        result = get_metrics(self.metrics_path)
        self.assertIn("a737abf", result["git_refs"])
        self.assertIn("09385c0", result["git_refs"])


class TestGetMetricsDropdownStability(unittest.TestCase):
    """Dropdown options (git_refs / models) must not shrink to the current
    selection when a ref/model filter is applied — otherwise the dropdown
    self-collapses and users can't switch to a different value without
    first clearing the selection.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.metrics_path = self.tmp_root / "metrics.jsonl"
        lines = [
            {
                "analysed_at": "2026-03-28T10:00:00.000Z",
                "git_ref": "aaa1111",
                "coordinator_model": "anthropic/claude-sonnet-4-6",
                "conversation_id": "c01", "date": "2026-03-28",
                "requests": 1, "steps": 1, "avg_steps": 1.0,
                "findings_by_mode": {}, "findings_by_severity": {}, "finding_count": 0,
            },
            {
                "analysed_at": "2026-03-29T10:00:00.000Z",
                "git_ref": "bbb2222",
                "coordinator_model": "anthropic/claude-haiku-4-5",
                "conversation_id": "c02", "date": "2026-03-29",
                "requests": 1, "steps": 1, "avg_steps": 1.0,
                "findings_by_mode": {}, "findings_by_severity": {}, "finding_count": 0,
            },
            {
                "analysed_at": "2026-03-30T10:00:00.000Z",
                "git_ref": "ccc3333",
                "coordinator_model": "gemini/gemini-2.5-pro",
                "conversation_id": "c03", "date": "2026-03-30",
                "requests": 1, "steps": 1, "avg_steps": 1.0,
                "findings_by_mode": {}, "findings_by_severity": {}, "finding_count": 0,
            },
        ]
        with self.metrics_path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_filtering_by_model_keeps_all_models_in_dropdown(self) -> None:
        result = get_metrics(self.metrics_path, model="anthropic/claude-sonnet-4-6")
        self.assertEqual(result["total_conversations"], 1)  # aggregates are filtered
        self.assertEqual(
            result["models"],
            [
                "anthropic/claude-haiku-4-5",
                "anthropic/claude-sonnet-4-6",
                "gemini/gemini-2.5-pro",
            ],
        )  # but dropdown options keep the full set

    def test_filtering_by_ref_keeps_all_refs_in_dropdown(self) -> None:
        result = get_metrics(self.metrics_path, ref="bbb")
        self.assertEqual(result["total_conversations"], 1)
        self.assertEqual(result["git_refs"], ["aaa1111", "bbb2222", "ccc3333"])

    def test_date_range_still_narrows_dropdown_options(self) -> None:
        # Date range is the display scope — dropdown options respect it
        # even though model/ref filters don't.
        result = get_metrics(self.metrics_path, after="2026-03-29")
        self.assertEqual(
            result["models"],
            ["anthropic/claude-haiku-4-5", "gemini/gemini-2.5-pro"],
        )
        self.assertEqual(result["git_refs"], ["bbb2222", "ccc3333"])


class TestGetMetricsUsageAggregation(unittest.TestCase):
    """Usage (tokens + cost) aggregation across the filtered metrics window.

    Powers the token-usage / cost / cache-hit-rate summary cards and charts
    on the metrics page.
    """

    def _write_entries(self, path: Path, entries: list[dict]) -> None:
        with path.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def _make_entry(self, cid: str, *, input_tokens: int = 0, output_tokens: int = 0,
                    cache_read: int = 0, cache_creation: int = 0,
                    cost_usd: float = 0.0, include_usage: bool = True) -> dict:
        entry: dict = {
            "analysed_at": "2026-04-17T10:00:00Z",
            "git_ref": "abc1234",
            "conversation_id": cid,
            "date": "2026-04-17",
            "requests": 1, "steps": 2, "avg_steps": 2.0,
            "findings_by_mode": {}, "findings_by_severity": {}, "finding_count": 0,
        }
        if include_usage:
            entry["usage"] = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "cost_usd": cost_usd,
            }
        return entry

    def test_sums_tokens_and_cost_across_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.jsonl"
            self._write_entries(path, [
                self._make_entry("c01", input_tokens=1000, output_tokens=200,
                                 cache_read=300, cost_usd=0.010),
                self._make_entry("c02", input_tokens=2000, output_tokens=400,
                                 cache_read=500, cache_creation=100, cost_usd=0.020),
            ])
            result = get_metrics(path)
            self.assertEqual(result["total_input_tokens"], 3000)
            self.assertEqual(result["total_output_tokens"], 600)
            self.assertEqual(result["total_cache_read_tokens"], 800)
            self.assertEqual(result["total_cache_creation_tokens"], 100)
            self.assertAlmostEqual(result["total_cost_usd"], 0.030, places=6)

    def test_cache_hit_rate_computed(self):
        """cache_hit_rate = cache_read / (cache_read + cache_creation + fresh_input),
        where fresh_input = input_tokens - cache_read. That denominator equals
        input_tokens + cache_creation (the total 'new work' the provider did).
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.jsonl"
            self._write_entries(path, [
                # 1000 input, 600 of which came from cache, 100 new cache writes.
                # Hit rate = 600 / (1000 + 100) = 0.5454...
                self._make_entry("c01", input_tokens=1000, cache_read=600, cache_creation=100),
            ])
            result = get_metrics(path)
            self.assertAlmostEqual(result["cache_hit_rate"], 600 / 1100, places=4)

    def test_cache_hit_rate_zero_when_no_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.jsonl"
            self._write_entries(path, [self._make_entry("c01")])
            result = get_metrics(path)
            self.assertEqual(result["cache_hit_rate"], 0.0)

    def test_entries_missing_usage_treated_as_zero(self):
        """Historical entries predating the usage-enrichment change should
        contribute zero, not raise.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.jsonl"
            self._write_entries(path, [
                self._make_entry("c01", input_tokens=1000, cost_usd=0.01),
                self._make_entry("c02", include_usage=False),  # legacy entry
            ])
            result = get_metrics(path)
            self.assertEqual(result["total_input_tokens"], 1000)
            self.assertAlmostEqual(result["total_cost_usd"], 0.01, places=6)

    def test_empty_entry_list_returns_zero_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("")
            result = get_metrics(path)
            self.assertEqual(result["total_input_tokens"], 0)
            self.assertEqual(result["total_cost_usd"], 0.0)
            self.assertEqual(result["cache_hit_rate"], 0.0)


# ---------------------------------------------------------------------------
# Tests — run_analysis
# ---------------------------------------------------------------------------


class _LockAcquired:
    """Context manager that yields True (lock acquired) for stubbing
    ``acquire_scan_lock`` in tests that don't care about lock semantics."""

    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False


class _LockBusy:
    """Context manager that yields False (lock contended)."""

    def __enter__(self):
        return False

    def __exit__(self, *exc):
        return False


def _patch_run_analysis_helpers(**overrides):
    """Build patches for run_analysis's analyser-side collaborators
    plus a ``prepare_context`` callable to inject. Override any of:
    prepare, resolve_conv, lock, run_single (single-conv result dict).

    Returns ``(ExitStack, prepare_context_callable)``. The stack
    patches the external collaborators imported into browser.py from
    ``analyser.analyse``; the callable is passed via the new
    ``prepare_context=`` parameter on ``run_analysis``."""
    from contextlib import ExitStack
    defaults = {
        "prepare": ("anthropic/claude-x", "k", "guide-text", "abc1234"),
        "resolve_conv": Path("/fake/conv/dir"),
        "lock": _LockAcquired(),
        "run_single": {"ok": True, "analysis": {"findings": []}},
    }
    defaults.update(overrides)
    stack = ExitStack()
    stack.enter_context(patch(
        "analyser.analyse.resolve_conversation_path",
        return_value=defaults["resolve_conv"],
    ))
    stack.enter_context(patch(
        "analyser.analyse.acquire_scan_lock", return_value=defaults["lock"],
    ))
    stack.enter_context(patch(
        "analyser.analyse.run_single_conversation_analysis",
        return_value=defaults["run_single"],
    ))
    prepare = defaults["prepare"]
    if callable(prepare):
        return stack, prepare
    return stack, (lambda: prepare)


class TestRunAnalysis(unittest.TestCase):
    """Wrapper-orchestration tests for ``run_analysis``.

    ``resolve_conversation_path``, ``acquire_scan_lock``, and
    ``run_single_conversation_analysis`` are external collaborators
    imported into ``app.analysis.browser`` from ``analyser.analyse``,
    each with its own coverage in ``test_analyser_*`` / ``test_analyse_*``.

    The settings + guide-text resolver is injected via the public
    ``prepare_context=`` parameter — no test-seam patching required.

    The wrapper's contract under test is the orchestration: lock
    handling, exception capture, status-dict translation.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.logs_root = self.tmp_root / "agent" / "logs" / "conversation"
        self.logs_root.mkdir(parents=True)
        _create_log_tree(self.logs_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_successful_rerun(self) -> None:
        stack, prep = _patch_run_analysis_helpers()
        with stack:
            result = run_analysis(
                self.logs_root, "2026-03-29", "c02", prepare_context=prep,
            )
            self.assertTrue(result["ok"])
            self.assertIn("analysis", result)

    def test_analyser_returns_error_surfaced_as_ok_false(self) -> None:
        stack, prep = _patch_run_analysis_helpers(
            run_single={"ok": False, "error": "LLM timed out"},
        )
        with stack:
            result = run_analysis(
                self.logs_root, "2026-03-29", "c02", prepare_context=prep,
            )
            self.assertFalse(result["ok"])
            self.assertIn("timed out", result["error"].lower())

    def test_lock_busy_returns_status_busy(self) -> None:
        stack, prep = _patch_run_analysis_helpers(lock=_LockBusy())
        with stack:
            result = run_analysis(
                self.logs_root, "2026-03-29", "c02", prepare_context=prep,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "busy")

    def test_conversation_not_found(self) -> None:
        stack, prep = _patch_run_analysis_helpers(resolve_conv=None)
        with stack:
            result = run_analysis(
                self.logs_root, "2026-03-29", "c02", prepare_context=prep,
            )
            self.assertFalse(result["ok"])
            self.assertIn("not found", result["error"].lower())

    def test_fatal_error_surfaced_as_ok_false(self) -> None:
        from analyser.analyse import AnalyserFatalError

        def _raises():
            raise AnalyserFatalError("no API key")

        stack, _ = _patch_run_analysis_helpers()
        with stack:
            result = run_analysis(
                self.logs_root, "2026-03-29", "c02", prepare_context=_raises,
            )
        self.assertFalse(result["ok"])
        self.assertIn("no API key", result["error"])


# ---------------------------------------------------------------------------
# Tests — scan_and_analyse
# ---------------------------------------------------------------------------


def _patch_scan_helpers(**overrides):
    """Patches for scan_and_analyse's analyser-side collaborators plus
    a ``prepare_context`` callable to inject. Returns
    ``(ExitStack, prepare_context_callable)``."""
    from contextlib import ExitStack
    defaults = {
        "prepare": ("anthropic/claude-x", "k", "guide-text", "abc1234"),
        "lock": _LockAcquired(),
    }
    defaults.update(overrides)
    stack = ExitStack()
    stack.enter_context(patch(
        "analyser.analyse.acquire_scan_lock", return_value=defaults["lock"],
    ))
    stack.enter_context(patch("analyser.analyse.process_all_pending_feedback"))
    stack.enter_context(patch("analyser.analyse.consolidate_lessons"))
    stack.enter_context(patch("analyser.analyse.collect_metrics"))
    # run_scan is replaced per-test below via stack.enter_context.
    prepare = defaults["prepare"]
    prepare_fn = prepare if callable(prepare) else (lambda: prepare)
    return stack, prepare_fn


class TestScanAndAnalyse(unittest.TestCase):
    """Wrapper-orchestration tests for ``scan_and_analyse``.

    Boundary mix:
    - ``_find_unanalysed_conversations`` (same module) — used REAL via
      the temp log tree set up in setUp / test bodies.
    - ``acquire_scan_lock``, ``process_all_pending_feedback``,
      ``consolidate_lessons``, ``run_scan``, ``collect_metrics`` —
      external collaborators from ``analyser.analyse`` with their own
      coverage; patched here.
    - The settings + guide-text resolver is injected via the public
      ``prepare_context=`` parameter.

    The wrapper's contract under test is the orchestration: batch_size
    passthrough, early-return when no unanalysed convs, lock contention
    → status=busy, fatal/general error → ok=False with surfaced error.
    """

    def setUp(self) -> None:
        from datetime import datetime as dt
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.logs_root = self.tmp_root / "agent" / "logs" / "conversation"
        self.logs_root.mkdir(parents=True)
        _create_log_tree(self.logs_root)
        self.today = dt.now().strftime("%Y-%m-%d")
        today_c03 = self.logs_root / self.today / "c03"
        today_c03.mkdir(parents=True, exist_ok=True)
        (today_c03 / "rq-c03-0001").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fake_run_scan(self, mark: list[tuple[str, str]]):
        """Build a fake run_scan that writes analysis.yaml for the given
        (date, conv_id) pairs — emulates what a successful real run does."""
        def _side_effect(*_args, **_kwargs):
            for date_str, conv_id in mark:
                (self.logs_root / date_str / conv_id / "analysis.yaml").write_text(
                    "findings: []\n",
                )
            return len(mark)
        return _side_effect

    def test_delegates_to_run_scan_with_batch_size(self) -> None:
        stack, prep = _patch_scan_helpers()
        with stack:
            mock_run = stack.enter_context(patch(
                "analyser.analyse.run_scan",
                side_effect=self._fake_run_scan([(self.today, "c03")]),
            ))
            result = scan_and_analyse(
                self.logs_root, batch_size=3, prepare_context=prep,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["analysed_count"], 1)
            self.assertEqual(result["errors"], [])
            args, _ = mock_run.call_args[0], mock_run.call_args[1]
            # Positional args: (model, api_key, guide, git_ref, staleness, batch_size)
            self.assertEqual(args[4], 0)  # staleness=0
            self.assertEqual(args[5], 3)  # batch_size=3

    def test_returns_zero_when_all_analysed(self) -> None:
        import shutil
        shutil.rmtree(self.logs_root / self.today / "c03")
        stack, prep = _patch_scan_helpers()
        with stack:
            mock_run = stack.enter_context(patch("analyser.analyse.run_scan"))
            result = scan_and_analyse(self.logs_root, prepare_context=prep)
            self.assertTrue(result["ok"])
            self.assertEqual(result["analysed_count"], 0)
            mock_run.assert_not_called()

    def test_lock_busy_reports_status_busy(self) -> None:
        stack, prep = _patch_scan_helpers(lock=_LockBusy())
        with stack:
            mock_run = stack.enter_context(patch("analyser.analyse.run_scan"))
            result = scan_and_analyse(self.logs_root, prepare_context=prep)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["analysed_count"], 0)
            mock_run.assert_not_called()

    def test_fatal_error_surfaced_as_ok_false(self) -> None:
        from analyser.analyse import AnalyserFatalError

        def _raises():
            raise AnalyserFatalError("no API key")

        result = scan_and_analyse(self.logs_root, prepare_context=_raises)
        self.assertFalse(result["ok"])
        self.assertIn("no API key", result["error"])

    def test_run_scan_exception_surfaced_as_error(self) -> None:
        stack, prep = _patch_scan_helpers()
        with stack:
            stack.enter_context(patch(
                "analyser.analyse.run_scan",
                side_effect=RuntimeError("network down"),
            ))
            result = scan_and_analyse(self.logs_root, prepare_context=prep)
            self.assertFalse(result["ok"])
            self.assertIn("network down", result["error"])


class TestListConversationRequestsUsage(unittest.TestCase):
    """Per-request summaries must include outcome.usage so the frontend
    can render cost and token data on request cards and aggregate up to
    the conversation-level stats row.
    """

    def _make_request(self, conv_dir: Path, rq_num: int, *,
                      cost_usd: float | None = None,
                      input_tokens: int = 1000, output_tokens: int = 200) -> None:
        conv_id = conv_dir.name
        rq_dir = conv_dir / f"rq-{conv_id}-{rq_num:04d}"
        rq_dir.mkdir(parents=True)
        (rq_dir / "request.json").write_text(json.dumps({
            "request_id": rq_dir.name, "user_input": "test",
            "timestamp": f"2026-04-18T10:{rq_num:02d}:00",
        }))
        outcome: dict = {
            "request_id": rq_dir.name, "status": "completed",
            "total_steps": 2, "total_duration_ms": 500,
        }
        if cost_usd is not None:
            outcome["usage"] = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cost_usd": cost_usd,
            }
        (rq_dir / "outcome.json").write_text(json.dumps(outcome))

    def test_summary_includes_usage_when_outcome_has_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            conv_dir = Path(tmp) / "2026-04-18" / "c01"
            conv_dir.mkdir(parents=True)
            self._make_request(conv_dir, 1, cost_usd=0.012)

            summaries = list_conversation_requests(Path(tmp), "2026-04-18", "c01")
            self.assertEqual(len(summaries), 1)
            self.assertIn("usage", summaries[0])
            self.assertAlmostEqual(summaries[0]["usage"]["cost_usd"], 0.012, places=6)
            self.assertEqual(summaries[0]["usage"]["input_tokens"], 1000)

    def test_summary_omits_usage_when_outcome_has_none(self):
        """Historical outcomes without a usage block should not add the
        key — the frontend will treat `undefined` as 'no data'.
        """
        with tempfile.TemporaryDirectory() as tmp:
            conv_dir = Path(tmp) / "2026-04-18" / "c01"
            conv_dir.mkdir(parents=True)
            self._make_request(conv_dir, 1, cost_usd=None)

            summaries = list_conversation_requests(Path(tmp), "2026-04-18", "c01")
            self.assertEqual(len(summaries), 1)
            self.assertNotIn("usage", summaries[0])


class TestFindUnanalysedConversations(unittest.TestCase):
    """_find_unanalysed_conversations walks every date within the log
    retention window (default 7 days) and returns conversations that
    lack an analysis.yaml — the scan button is an explicit "analyse now"
    with no staleness gate.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _date_n_days_ago(self, n: int) -> str:
        from datetime import datetime, timedelta
        return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")

    def _today(self) -> str:
        return self._date_n_days_ago(0)

    def _make_conv(self, date_str: str, conv_id: str, *, analysed: bool = False) -> None:
        conv_dir = self.logs_root / date_str / conv_id
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "rq-test-0001").mkdir()
        if analysed:
            (conv_dir / "analysis.yaml").write_text("topic: test\n")

    def test_returns_todays_unanalysed(self):
        today = self._today()
        self._make_conv(today, "c01", analysed=True)
        self._make_conv(today, "c02")
        self._make_conv(today, "c03")
        result = _find_unanalysed_conversations(self.logs_root)
        self.assertEqual(result, [(today, "c02"), (today, "c03")])

    def test_returns_yesterdays_unanalysed(self):
        """Conversations created near midnight should be picked up on the
        next day's scan — the previous today-only scope missed these.
        """
        yesterday = self._date_n_days_ago(1)
        self._make_conv(yesterday, "c21")
        result = _find_unanalysed_conversations(self.logs_root)
        self.assertEqual(result, [(yesterday, "c21")])

    def test_returns_unanalysed_within_retention_window(self):
        """Walks the full retention window (default 7 days)."""
        three_days_ago = self._date_n_days_ago(3)
        six_days_ago = self._date_n_days_ago(6)
        self._make_conv(three_days_ago, "c10")
        self._make_conv(six_days_ago, "c05")
        result = _find_unanalysed_conversations(self.logs_root)
        # Results sort by date descending, then conversation id
        self.assertIn((three_days_ago, "c10"), result)
        self.assertIn((six_days_ago, "c05"), result)

    def test_ignores_dates_outside_retention_window(self):
        """Default 7-day window — 30 days ago is outside and must be skipped."""
        thirty_days_ago = self._date_n_days_ago(30)
        self._make_conv(thirty_days_ago, "c01")
        result = _find_unanalysed_conversations(self.logs_root)
        self.assertEqual(result, [])

    def test_ignores_conversations_without_requests(self):
        today = self._today()
        conv_dir = self.logs_root / today / "c01"
        conv_dir.mkdir(parents=True)
        result = _find_unanalysed_conversations(self.logs_root)
        self.assertEqual(result, [])

    def test_empty_logs_root(self):
        result = _find_unanalysed_conversations(self.logs_root)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# get_request_logs
# ---------------------------------------------------------------------------


class TestGetRequestLogs(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _create_request(self, date, conv_id, rq_id, *, request=None, outcome=None, tools=None):
        req_dir = self.logs_root / date / conv_id / rq_id
        req_dir.mkdir(parents=True)
        if request:
            (req_dir / "request.json").write_text(json.dumps(request))
        if outcome:
            (req_dir / "outcome.json").write_text(json.dumps(outcome))
        if tools:
            tool_dir = req_dir / "tool_executions"
            tool_dir.mkdir()
            for i, tool in enumerate(tools, 1):
                name = tool.get("selected_skill", "unknown")
                (tool_dir / f"{i:02d}_{name}.json").write_text(json.dumps(tool))

    def test_returns_all_fields(self):
        self._create_request(
            "2026-03-30", "c17", "rq-c17-0001",
            request={"request_id": "rq-c17-0001", "user_input": "Play something"},
            outcome={"status": "completed", "chat_response": "Done!", "total_steps": 2},
            tools=[
                {"step": 1, "selected_skill": "roon_search", "tool_input": {"operation": "new_search"}, "tool_output": {"groups": []}, "duration_ms": 100},
                {"step": 2, "selected_skill": "roon_action", "tool_input": {"action": "Play Now"}, "tool_output": {"result": "OK"}, "duration_ms": 200},
            ],
        )
        result = get_request_logs(self.logs_root, "2026-03-30", "c17", "rq-c17-0001")
        self.assertIsNotNone(result)
        self.assertEqual(result["request_id"], "rq-c17-0001")
        self.assertEqual(result["request"]["user_input"], "Play something")
        self.assertEqual(result["outcome"]["status"], "completed")
        self.assertEqual(len(result["tool_executions"]), 2)
        self.assertEqual(result["tool_executions"][0]["selected_skill"], "roon_search")
        self.assertEqual(result["tool_executions"][1]["selected_skill"], "roon_action")

    def test_missing_request_dir_returns_none(self):
        result = get_request_logs(self.logs_root, "2026-03-30", "c17", "rq-c17-9999")
        self.assertIsNone(result)

    def test_missing_optional_files(self):
        """Request dir with no files still returns a valid structure."""
        req_dir = self.logs_root / "2026-03-30" / "c01" / "rq-c01-0001"
        req_dir.mkdir(parents=True)
        result = get_request_logs(self.logs_root, "2026-03-30", "c01", "rq-c01-0001")
        self.assertIsNotNone(result)
        self.assertNotIn("request", result)
        self.assertNotIn("outcome", result)
        self.assertEqual(result["tool_executions"], [])

    def test_tool_executions_sorted_by_filename(self):
        self._create_request(
            "2026-03-30", "c01", "rq-c01-0001",
            tools=[
                {"step": 2, "selected_skill": "roon_action", "tool_input": {}, "tool_output": {}},
                {"step": 1, "selected_skill": "roon_search", "tool_input": {}, "tool_output": {}},
            ],
        )
        # Files are named 01_roon_search.json and 02_roon_action.json
        # but we create them as 01_roon_action and 02_roon_search (based on enumerate order)
        # The function sorts by filename, so order depends on filenames not creation order
        result = get_request_logs(self.logs_root, "2026-03-30", "c01", "rq-c01-0001")
        self.assertEqual(len(result["tool_executions"]), 2)


# ---------------------------------------------------------------------------
# get_result_handle_data
# ---------------------------------------------------------------------------


class TestGetResultHandleData(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.logs_root = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_request(
        self,
        date: str,
        conv_id: str,
        rq_id: str,
        *,
        system_prompt: str | None = None,
        tool_executions: list[dict] | None = None,
    ) -> Path:
        req_dir = self.logs_root / date / conv_id / rq_id
        req_dir.mkdir(parents=True, exist_ok=True)
        if system_prompt is not None:
            prompts_dir = req_dir / "prompts"
            prompts_dir.mkdir(exist_ok=True)
            (prompts_dir / "coordinator_system.txt").write_text(
                system_prompt, encoding="utf-8"
            )
        if tool_executions is not None:
            tool_dir = req_dir / "tool_executions"
            tool_dir.mkdir(exist_ok=True)
            for i, te in enumerate(tool_executions, 1):
                skill = te.get("selected_skill", "unknown")
                (tool_dir / f"{i:02d}_{skill}.json").write_text(
                    json.dumps(te), encoding="utf-8"
                )
        return req_dir

    def test_found_with_history_and_items(self):
        """Both search history line and result_fetch items found."""
        prompt = (
            "## Search History\n"
            "[res_00001] 11:38 | Roon: Favourites 2 playlist tracks | 14 items\n"
        )
        self._make_request("2026-03-31", "c04", "rq-c04-0001", system_prompt=prompt)
        self._make_request(
            "2026-03-31", "c04", "rq-c04-0002",
            system_prompt=prompt,
            tool_executions=[{
                "selected_skill": "result_fetch",
                "tool_input": {"result_handle": "res_00001"},
                "tool_output": {
                    "result_handle": "res_00001",
                    "items": ["(0) Play Playlist", "(1) Track A", "(2) Track B"],
                },
            }],
        )

        result = get_result_handle_data(
            self.logs_root, "2026-03-31", "c04", "res_00001"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["result_handle"], "res_00001")
        self.assertIn("res_00001", result["search_history_line"])
        self.assertIn("Favourites 2", result["search_history_line"])
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["source_request_id"], "rq-c04-0002")

    def test_history_only_no_fetch(self):
        """Search history exists but result_fetch was never called."""
        prompt = (
            "## Search History\n"
            "[res_00003] 10:00 | Roon: Jazz albums | 8 items\n"
        )
        self._make_request("2026-03-31", "c01", "rq-c01-0002", system_prompt=prompt)

        result = get_result_handle_data(
            self.logs_root, "2026-03-31", "c01", "res_00003"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["search_history_line"], "[res_00003] 10:00 | Roon: Jazz albums | 8 items")
        self.assertIsNone(result["items"])
        self.assertIsNone(result["source_request_id"])

    def test_not_found(self):
        """Handle doesn't exist in any logs."""
        self._make_request("2026-03-31", "c01", "rq-c01-0001")
        result = get_result_handle_data(
            self.logs_root, "2026-03-31", "c01", "res_99999"
        )
        self.assertIsNone(result)

    def test_nonexistent_conversation(self):
        """Conversation directory doesn't exist at all."""
        result = get_result_handle_data(
            self.logs_root, "2026-03-31", "c99", "res_00001"
        )
        self.assertIsNone(result)
