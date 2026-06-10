"""Browse and serve passive analysis results from conversation log directories."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from app.analysis.feedback import FEEDBACK_FILENAME
from app.coordinator.parse_helpers import safe_parse_yaml, safe_parse_yaml_list

_log = logging.getLogger("swarpius.analysis_browser")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_analysed_conversations(
    logs_root: Path,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Scan log directories for analysis.yaml files and return a sorted list.

    Returns a dict with ``conversations`` (sorted by date descending then
    conversation ID descending) and ``models`` (unique coordinator models
    across the date range).  Conversations without an analysis.yaml or
    with unparseable YAML are silently skipped.

    Optional *date_from* / *date_to* (YYYY-MM-DD strings) restrict the scan
    to date directories within the inclusive range.  *model* filters to
    conversations using that coordinator model.
    """
    if not logs_root.is_dir():
        return {"conversations": [], "models": []}

    results: list[dict[str, Any]] = []
    for date_dir in logs_root.iterdir():
        if not date_dir.is_dir():
            continue
        date_str = date_dir.name
        if date_from and date_str < date_from:
            continue
        if date_to and date_str > date_to:
            continue
        for conv_dir in date_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            analysis_path = conv_dir / "analysis.yaml"
            if not analysis_path.exists():
                continue
            parsed = safe_parse_yaml(analysis_path)
            if parsed is None:
                continue
            results.append(_to_list_entry(parsed, date_str, conv_dir.name, conv_dir))

    results.sort(key=lambda r: (r["date"], r["conversation_id"]), reverse=True)

    # Collect unique models before filtering
    models = sorted({r["coordinator_model"] for r in results if r.get("coordinator_model")})

    if model:
        results = [r for r in results if r.get("coordinator_model", "") == model]

    return {"conversations": results, "models": models}


def get_list_entry(
    logs_root: Path, date: str, conversation_id: str
) -> dict[str, Any] | None:
    """Return the list-view metadata for a single conversation, or None."""
    conv_dir = logs_root / date / conversation_id
    analysis_path = conv_dir / "analysis.yaml"
    if not analysis_path.exists():
        return None
    parsed = safe_parse_yaml(analysis_path)
    if parsed is None:
        return None
    return _to_list_entry(parsed, date, conversation_id, conv_dir)


def get_analysis_detail(
    logs_root: Path, date: str, conversation_id: str
) -> dict[str, Any] | None:
    """Return the full parsed analysis for a specific conversation, or None.

    Includes an ``history`` key with prior analysis snapshots (empty list if
    no history file exists).
    """
    analysis_path = logs_root / date / conversation_id / "analysis.yaml"
    if not analysis_path.exists():
        return None
    analysis = safe_parse_yaml(analysis_path)
    if analysis is None:
        return None

    history_path = logs_root / date / conversation_id / "analysis-history.yaml"
    analysis["history"] = safe_parse_yaml_list(history_path)
    analysis["requests"] = list_conversation_requests(logs_root, date, conversation_id)
    return analysis


DEFAULT_ON_DEMAND_BATCH_SIZE = 5


def _resolve_batch_size() -> int:
    """Resolve scan batch size from ``ANALYSER_BATCH_SIZE`` at call time.

    Shared env contract with the analyser's ``resolve_batch_size`` so
    the scheduled loop and the on-demand path honour the same value.
    """
    from app.settings import get_settings
    value = get_settings().analyser_batch_size
    return value if value > 0 else DEFAULT_ON_DEMAND_BATCH_SIZE


def _default_prepare_context() -> tuple[Any, ...]:
    from analyser.analyse import prepare_context
    return prepare_context()


def run_analysis(
    logs_root: Path,
    date: str,
    conversation_id: str,
    *,
    prepare_context: Optional[Callable[[], tuple[Any, ...]]] = None,
) -> dict[str, Any]:
    """Re-analyse a specific conversation in-process.

    Calls the analyser's ``run_single_conversation_analysis`` directly
    under its scan lock. Returns ``{"ok": True, "analysis": ...}`` on
    success, ``{"ok": True, "status": "busy"}`` when the lock is
    contended (background loop or another concurrent click), or
    ``{"ok": False, "error": ...}`` otherwise. The lock contention
    case stays ``ok=True`` so the frontend treats it as a retry-able
    user message, not an error.
    """
    try:
        from analyser.analyse import (
            AnalyserFatalError,
            acquire_scan_lock,
            resolve_conversation_path,
            run_single_conversation_analysis,
        )
    except ImportError as exc:
        _log.error("Analyser package not importable: %s", exc)
        return {"ok": False, "error": "Analyser not available"}

    conv_dir = resolve_conversation_path(f"{date}/{conversation_id}")
    if conv_dir is None:
        return {
            "ok": False,
            "error": f"Conversation not found: {date}/{conversation_id}",
        }

    prepare = prepare_context or _default_prepare_context
    try:
        model, api_key, guide_text, git_ref = prepare()
    except AnalyserFatalError as exc:
        return {"ok": False, "error": str(exc)}

    try:
        with acquire_scan_lock() as acquired:
            if not acquired:
                _log.info("Re-analyse skipped — another scan is in progress")
                return {"ok": True, "status": "busy"}
            result = run_single_conversation_analysis(
                model, api_key, conv_dir, guide_text, git_ref,
            )
    except AnalyserFatalError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        _log.exception(
            "In-process re-analysis failed for %s/%s", date, conversation_id,
        )
        return {"ok": False, "error": str(exc)}

    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "Analysis failed — check logs.",
        }
    detail = get_analysis_detail(logs_root, date, conversation_id)
    return {"ok": True, "analysis": detail}


def scan_and_analyse(
    logs_root: Path,
    batch_size: Optional[int] = None,
    *,
    prepare_context: Optional[Callable[[], tuple[Any, ...]]] = None,
) -> dict[str, Any]:
    """Find conversations without analysis.yaml and analyse them in-process.

    Mirrors the analyser CLI's single-pass scan path: process pending
    feedback, consolidate lessons, then ``run_scan`` over unanalysed
    conversations under the scan lock. The scan lock is shared with
    the background loop (when enabled), so concurrent invocations get
    ``status=busy`` rather than racing.

    Returns ``{"ok": True, "analysed_count": int, "errors": list}``
    on success, ``{"ok": True, "status": "busy", ...}`` when the lock
    is contended, or ``{"ok": False, "error": str}`` on failure.
    """
    try:
        from analyser.analyse import (
            AnalyserFatalError,
            acquire_scan_lock,
            collect_metrics,
            consolidate_lessons,
            process_all_pending_feedback,
            run_scan,
        )
    except ImportError as exc:
        _log.error("Analyser package not importable: %s", exc)
        return {"ok": False, "error": "Analyser not available"}

    unanalysed = _find_unanalysed_conversations(logs_root)
    _log.info("Scan found %d unanalysed conversation(s)", len(unanalysed))
    if not unanalysed:
        return {"ok": True, "analysed_count": 0, "errors": []}

    if batch_size is None:
        batch_size = _resolve_batch_size()

    prepare = prepare_context or _default_prepare_context
    try:
        model, api_key, guide_text, git_ref = prepare()
    except AnalyserFatalError as exc:
        return {"ok": False, "error": str(exc)}

    try:
        with acquire_scan_lock() as acquired:
            if not acquired:
                _log.info("Scan skipped — another scan is in progress")
                return {"ok": True, "status": "busy", "analysed_count": 0, "errors": []}
            process_all_pending_feedback(model, api_key, guide_text, git_ref)
            consolidate_lessons(model, api_key)
            # staleness=0: pick up everything unanalysed regardless of age.
            run_scan(model, api_key, guide_text, git_ref, 0, batch_size)
            collect_metrics()
    except AnalyserFatalError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        _log.exception("In-process scan failed")
        return {"ok": False, "error": str(exc)}

    still_unanalysed = _find_unanalysed_conversations(logs_root)
    analysed_count = max(0, len(unanalysed) - len(still_unanalysed))
    _log.info("Scan complete: %d analysed", analysed_count)
    return {"ok": True, "analysed_count": analysed_count, "errors": []}


def list_conversation_requests(
    logs_root: Path, date: str, conversation_id: str
) -> list[dict[str, Any]]:
    """Return a summary list of all requests in a conversation."""
    conv_dir = logs_root / date / conversation_id
    if not conv_dir.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for req_dir in sorted(conv_dir.iterdir()):
        if not req_dir.is_dir() or not req_dir.name.startswith("rq-"):
            continue
        summary: dict[str, Any] = {"request_id": req_dir.name}
        request_data = _safe_parse_json(req_dir / "request.json")
        if request_data:
            summary["user_input"] = request_data.get("user_input")
            summary["timestamp"] = request_data.get("timestamp")
        outcome_data = _safe_parse_json(req_dir / "outcome.json")
        if outcome_data:
            summary["status"] = outcome_data.get("status")
            summary["total_steps"] = outcome_data.get("total_steps")
            summary["total_duration_ms"] = outcome_data.get("total_duration_ms")
            if outcome_data.get("coordinator_model"):
                summary["coordinator_model"] = outcome_data["coordinator_model"]
            if outcome_data.get("usage"):
                summary["usage"] = outcome_data["usage"]
        summaries.append(summary)
    return summaries


def get_request_logs(
    logs_root: Path, date: str, conversation_id: str, request_id: str
) -> dict[str, Any] | None:
    """Return structured log data for a specific request.

    Returns a dict with ``request``, ``outcome``, and ``tool_executions``
    keys, or None if the request directory doesn't exist.
    """
    req_dir = logs_root / date / conversation_id / request_id
    if not req_dir.is_dir():
        return None

    result: dict[str, Any] = {"request_id": request_id}

    # request.json
    request_file = req_dir / "request.json"
    if request_file.exists():
        result["request"] = _safe_parse_json(request_file)

    # outcome.json
    outcome_file = req_dir / "outcome.json"
    if outcome_file.exists():
        result["outcome"] = _safe_parse_json(outcome_file)

    # tool_executions/*.json — sorted by filename
    tool_dir = req_dir / "tool_executions"
    executions: list[dict[str, Any]] = []
    if tool_dir.is_dir():
        for tf in sorted(tool_dir.glob("*.json")):
            parsed = _safe_parse_json(tf)
            if parsed is not None:
                executions.append(parsed)
    result["tool_executions"] = executions

    # coordinator_steps/*.yaml — sorted by filename
    steps_dir = req_dir / "coordinator_steps"
    steps: list[dict[str, Any]] = []
    if steps_dir.is_dir():
        for sf in sorted(steps_dir.glob("*.yaml")):
            parsed = safe_parse_yaml(sf)
            if parsed is not None:
                steps.append(parsed)
    result["coordinator_steps"] = steps

    # prompts/* — all files, sorted by name
    prompts_dir = req_dir / "prompts"
    prompts: dict[str, str] = {}
    if prompts_dir.is_dir():
        for pf in sorted(prompts_dir.iterdir()):
            if pf.is_file():
                try:
                    prompts[pf.name] = pf.read_text(encoding="utf-8")
                except Exception:
                    _log.debug("Failed to read prompt file: %s", pf, exc_info=True)
    result["prompts"] = prompts

    return result


def get_result_handle_data(
    logs_root: Path,
    date: str,
    conversation_id: str,
    result_handle: str,
) -> dict[str, Any] | None:
    """Find data for a result handle in conversation logs.

    Scans request directories in the conversation to find:
    1. The search history line from coordinator system prompts
    2. The full item list from any result_fetch tool execution

    Returns a dict with ``result_handle``, ``search_history_line``,
    ``items``, and ``source_request_id``, or None if the handle is
    not found anywhere.
    """
    conv_dir = logs_root / date / conversation_id
    if not conv_dir.is_dir():
        return None

    search_history_line: str | None = None
    items: list[str] | None = None
    source_request_id: str | None = None
    handle_tag = f"[{result_handle}]"

    req_dirs = sorted(
        [d for d in conv_dir.iterdir() if d.is_dir() and d.name.startswith("rq-")],
        key=lambda d: d.name,
    )

    for req_dir in req_dirs:
        # Search system prompt for search history entry
        prompt_file = req_dir / "prompts" / "coordinator_system.txt"
        if prompt_file.exists():
            try:
                prompt_text = prompt_file.read_text(encoding="utf-8")
                for line in prompt_text.splitlines():
                    if handle_tag in line:
                        search_history_line = line.strip()
                        break
            except OSError:
                _log.debug("Failed to read prompt file: %s", prompt_file, exc_info=True)

        # Search tool_executions for result_fetch with this handle
        if items is None:
            tool_dir = req_dir / "tool_executions"
            if tool_dir.is_dir():
                for tf in sorted(tool_dir.glob("*.json")):
                    td = _safe_parse_json(tf)
                    if td is None:
                        continue
                    if td.get("selected_skill") == "result_fetch":
                        if td.get("tool_input", {}).get("result_handle") == result_handle:
                            output = td.get("tool_output", {})
                            if isinstance(output, dict) and output.get("items"):
                                items = output["items"]
                                source_request_id = req_dir.name
                                break

    if not search_history_line and items is None:
        return None

    return {
        "result_handle": result_handle,
        "search_history_line": search_history_line,
        "items": items,
        "source_request_id": source_request_id,
    }


def get_metrics(
    metrics_path: Path,
    after: str | None = None,
    before: str | None = None,
    ref: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Read metrics.jsonl and return aggregated data with optional filters.

    Filters:
        after  — include only entries with date >= after
        before — include only entries with date <= before
        ref    — include only entries whose git_ref starts with ref
        model  — include only entries whose coordinator_model matches
    """
    entries = _read_metrics_entries(metrics_path)

    # Date range is the display scope. Apply it first so dropdown options
    # reflect only the selected period.
    if after:
        entries = [e for e in entries if e.get("date", "") >= after]
    if before:
        entries = [e for e in entries if e.get("date", "") <= before]

    # Capture dropdown options from the date-scoped entries BEFORE applying
    # ref/model filters. Otherwise the dropdowns self-collapse: selecting
    # model X returns entries only for X, and the model dropdown then only
    # offers X — the user can't switch to a different model without first
    # clearing the selection.
    scope_entries = entries

    if ref:
        entries = [e for e in entries if e.get("git_ref", "").startswith(ref)]
    if model:
        entries = [e for e in entries if e.get("coordinator_model", "") == model]

    result = _aggregate_metrics(entries)
    result["git_refs"] = sorted({
        e.get("git_ref", "")
        for e in scope_entries
        if e.get("git_ref")
    })
    result["models"] = sorted({
        e.get("coordinator_model", "")
        for e in scope_entries
        if e.get("coordinator_model")
    })
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_parse_json(path: Path) -> dict[str, Any] | None:
    """Parse a JSON file, returning None on any error."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        _log.debug("Failed to parse %s", path, exc_info=True)
        return None


def _to_list_entry(
    parsed: dict[str, Any], date: str, conv_id: str, conv_dir: Path
) -> dict[str, Any]:
    """Extract list-view metadata from a parsed analysis dict."""
    findings = parsed.get("findings") or []
    severity_counts: Counter[str] = Counter()
    for f in findings:
        sev = f.get("severity")
        if sev:
            severity_counts[sev] += 1

    # Feedback metadata
    feedback_count = 0
    pending_feedback = 0
    fb_path = conv_dir / FEEDBACK_FILENAME
    if fb_path.exists():
        fb_data = safe_parse_yaml_list(fb_path)
        feedback_count = len(fb_data)
        pending_feedback = sum(
            1 for item in fb_data if item.get("lesson_status") == "pending"
        )

    # Analysis revision count (history entries + current)
    history = safe_parse_yaml_list(conv_dir / "analysis-history.yaml")
    analysis_revisions = len(history) + 1

    # Timestamp and model from the first user request in this conversation;
    # cost aggregated across every request outcome so the list card can
    # show a per-conversation spend alongside other metrics.
    first_request_at = ""
    coordinator_model = ""
    total_cost_usd = 0.0
    req_dirs = sorted(
        (d for d in conv_dir.iterdir() if d.is_dir() and d.name.startswith("rq-")),
        key=lambda d: d.name,
    )
    if req_dirs:
        req_data = _safe_parse_json(req_dirs[0] / "request.json")
        if req_data:
            first_request_at = req_data.get("timestamp", "")
        for req_dir in req_dirs:
            outcome_data = _safe_parse_json(req_dir / "outcome.json")
            if not outcome_data:
                continue
            if not coordinator_model:
                coordinator_model = outcome_data.get("coordinator_model", "")
            usage = outcome_data.get("usage") or {}
            cost = usage.get("cost_usd")
            if isinstance(cost, (int, float)):
                total_cost_usd += float(cost)

    return {
        "date": date,
        "conversation_id": conv_id,
        "first_request_at": first_request_at,
        "topic": parsed.get("topic", ""),
        "requests_analysed": parsed.get("requests_analysed", 0),
        "total_steps": parsed.get("total_steps", 0),
        "avg_steps_per_request": parsed.get("avg_steps_per_request", 0.0),
        "total_tool_calls": parsed.get("total_tool_calls", 0),
        "total_cost_usd": total_cost_usd,
        "git_ref": parsed.get("git_ref", ""),
        "finding_count": len(findings),
        "severity_summary": dict(severity_counts),
        "analysed_at": parsed.get("analysed_at", ""),
        "feedback_count": feedback_count,
        "pending_feedback": pending_feedback,
        "analysis_revisions": analysis_revisions,
        "coordinator_model": coordinator_model,
    }


def _find_unanalysed_conversations(logs_root: Path) -> list[tuple[str, str]]:
    """Find conversation directories lacking an analysis.yaml within the
    log retention window.

    Walks every date directory from today back to LOG_RETENTION_DAYS days
    ago (default 7). The scan button is an explicit "analyse now" action
    with no staleness gate — the only bound is the retention window,
    since anything older has been (or will be) cleaned up.
    """
    if not logs_root.is_dir():
        return []

    from app.settings import get_settings
    retention_days = get_settings().log_retention_days

    today = datetime.now()
    unanalysed: list[tuple[str, str]] = []
    for day_offset in range(retention_days):
        date_str = (today - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        date_dir = logs_root / date_str
        if not date_dir.is_dir():
            continue
        for conv_dir in sorted(date_dir.iterdir()):
            if not conv_dir.is_dir():
                continue
            if (conv_dir / "analysis.yaml").exists():
                continue
            has_requests = any(
                d.is_dir() and d.name.startswith("rq-")
                for d in conv_dir.iterdir()
            )
            if has_requests:
                unanalysed.append((date_str, conv_dir.name))
    return unanalysed


def _read_metrics_entries(metrics_path: Path) -> list[dict[str, Any]]:
    """Read metrics.jsonl and return a list of entry dicts."""
    if not metrics_path.is_file():
        return []

    entries: list[dict[str, Any]] = []
    try:
        with metrics_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    _log.debug("Skipping malformed metrics line: %s", line[:80])
    except Exception:
        _log.debug("Failed to read %s", metrics_path, exc_info=True)
    return entries


def _aggregate_metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a list of metrics entries into summary stats."""
    base: dict[str, Any] = {
        "total_conversations": 0,
        "total_findings": 0,
        "total_revoked": 0,
        "findings_by_severity": {},
        "findings_by_mode": {},
        "revoked_by_mode": {},
        "avg_steps_per_request": 0.0,
        "git_refs": [],
        "models": [],
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cost_usd": 0.0,
        "cache_hit_rate": 0.0,
        "entries": [],
    }
    if not entries:
        return base

    total_findings = 0
    total_revoked = 0
    severity_totals: Counter[str] = Counter()
    mode_totals: Counter[str] = Counter()
    revoked_mode_totals: Counter[str] = Counter()
    avg_steps_values: list[float] = []
    git_refs: set[str] = set()
    models: set[str] = set()
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0
    total_cost = 0.0

    for entry in entries:
        total_findings += entry.get("finding_count", 0)
        total_revoked += entry.get("revoked_count", 0)
        for sev, count in entry.get("findings_by_severity", {}).items():
            severity_totals[sev] += count
        for mode, count in entry.get("findings_by_mode", {}).items():
            mode_totals[mode] += count
        for mode, count in entry.get("revoked_by_mode", {}).items():
            revoked_mode_totals[mode] += count
        avg = entry.get("avg_steps")
        if avg is not None:
            avg_steps_values.append(avg)
        ref = entry.get("git_ref")
        if ref:
            git_refs.add(ref)
        model = entry.get("coordinator_model")
        if model:
            models.add(model)
        usage = entry.get("usage") or {}
        total_input += int(usage.get("input_tokens") or 0)
        total_output += int(usage.get("output_tokens") or 0)
        total_cache_read += int(usage.get("cache_read_input_tokens") or 0)
        total_cache_creation += int(usage.get("cache_creation_input_tokens") or 0)
        cost = usage.get("cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)

    overall_avg = sum(avg_steps_values) / len(avg_steps_values) if avg_steps_values else 0.0

    # cache_hit_rate denominator = input_tokens + cache_creation (all "new
    # work" the provider processed). Input includes the cache-read portion,
    # which is exactly the numerator.
    cache_denominator = total_input + total_cache_creation
    cache_hit_rate = total_cache_read / cache_denominator if cache_denominator > 0 else 0.0

    return {
        "total_conversations": len(entries),
        "total_findings": total_findings,
        "total_revoked": total_revoked,
        "findings_by_severity": dict(severity_totals),
        "findings_by_mode": dict(mode_totals),
        "revoked_by_mode": dict(revoked_mode_totals),
        "avg_steps_per_request": round(overall_avg, 2),
        "git_refs": sorted(git_refs),
        "models": sorted(models),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_creation_tokens": total_cache_creation,
        "total_cost_usd": total_cost,
        "cache_hit_rate": cache_hit_rate,
        "entries": entries,
    }
