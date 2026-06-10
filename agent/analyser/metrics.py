#!/usr/bin/env python3
"""Metrics collection and summary for passive conversation analysis.

Usage:
    python metrics.py collect              # Backfill metrics.jsonl from analysis files
    python metrics.py summary              # Show overall summary
    python metrics.py summary --after 2026-03-28
    python metrics.py summary --ref 09385c0
    python metrics.py summary --compare 09385c0 4d724ea
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent


try:
    # Bundle-aware resolver — must match where conversations are written.
    from app.data_paths import data_dir as _data_dir
except Exception:
    def _data_dir() -> Path:
        raw = os.environ.get("SWARPIUS_DATA_DIR", "")
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else AGENT_DIR / p
        return AGENT_DIR / "data"


METRICS_FILE = _data_dir() / "analysis" / "metrics.jsonl"
LOGS_ROOT = _data_dir() / "logs" / "conversation"


# ── Collect ──────────────────────────────────────────────────────────────────


def find_analysis_files() -> list[Path]:
    """Find all analysis.yaml files under the conversation logs."""
    if not LOGS_ROOT.is_dir():
        return []
    return sorted(LOGS_ROOT.glob("*/c*/analysis.yaml"))


def _load_analysis(path: Path) -> dict | None:
    """Load an analysis.yaml file."""
    if yaml is None:
        print(f"  Skipping {path}: pyyaml not installed")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Skipping {path}: {e}")
        return None


def load_existing_entries() -> dict[str, dict]:
    """Load entries from metrics.jsonl keyed by 'date:conversation_id'."""
    entries: dict[str, dict] = {}
    if METRICS_FILE.exists():
        for line in METRICS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                key = f"{entry['date']}:{entry['conversation_id']}"
                entries[key] = entry
            except (json.JSONDecodeError, KeyError):
                continue
    return entries


def _get_conversation_model(conv_dir: Path) -> str | None:
    """Read the coordinator model from the first request's outcome.json."""
    if not conv_dir.is_dir():
        return None
    for req_dir in sorted(conv_dir.iterdir()):
        if not req_dir.is_dir() or not req_dir.name.startswith("rq-"):
            continue
        outcome_path = req_dir / "outcome.json"
        if outcome_path.exists():
            try:
                data = json.loads(outcome_path.read_text(encoding="utf-8"))
                model = data.get("coordinator_model")
                if model:
                    return model
            except (json.JSONDecodeError, OSError):
                # Bad outcome.json — keep scanning sibling requests
                # rather than abort the whole conversation lookup.
                pass
    return None


_USAGE_INT_FIELDS = (
    "input_tokens", "output_tokens", "total_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
)


def _aggregate_conversation_usage(conv_dir: Path) -> dict:
    """Sum usage fields from every rq-*/outcome.json under a conversation.

    Returns a dict with the five int token fields + cost_usd (float).
    Missing files, missing usage blocks, and malformed JSON all contribute
    zero rather than raising — historical data predating the cost-tracking
    feature simply produces zero aggregates.
    """
    totals: dict = {k: 0 for k in _USAGE_INT_FIELDS}
    totals["cost_usd"] = 0.0

    if not conv_dir.is_dir():
        return totals

    for req_dir in sorted(conv_dir.iterdir()):
        if not req_dir.is_dir() or not req_dir.name.startswith("rq-"):
            continue
        outcome_path = req_dir / "outcome.json"
        if not outcome_path.exists():
            continue
        try:
            data = json.loads(outcome_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        usage = data.get("usage") or {}
        for field in _USAGE_INT_FIELDS:
            value = usage.get(field)
            if isinstance(value, int):
                totals[field] += value
        cost = usage.get("cost_usd")
        if isinstance(cost, (int, float)):
            totals["cost_usd"] += float(cost)

    return totals


def extract_metrics_line(
    analysis: dict,
    model: str | None = None,
    conv_dir: Path | None = None,
) -> str:
    """Convert an analysis.json dict to a single metrics JSONL line.

    When ``conv_dir`` is provided, per-request outcome.json files under
    that directory are summed into a per-conversation ``usage`` block
    (tokens, cache read/creation, cost_usd). Missing conv_dir yields a
    zero-filled usage block so the output schema stays stable.
    """
    findings = analysis.get("findings", [])
    by_mode: dict[str, int] = Counter()
    by_severity: dict[str, int] = Counter()
    for f in findings:
        by_mode[f["failure_mode"]] += 1
        by_severity[f["severity"]] += 1

    revoked = analysis.get("revoked_findings", []) or []
    revoked_by_mode: dict[str, int] = Counter()
    revoked_count = 0
    for r in revoked:
        # Only count revocations that matched a finding (and so were
        # enriched with `original_finding` by _apply_revocations).
        # Unmatched entries are noise from the analyser.
        original = r.get("original_finding") if isinstance(r, dict) else None
        if not isinstance(original, dict):
            continue
        revoked_count += 1
        mode = original.get("failure_mode")
        if mode:
            revoked_by_mode[mode] += 1

    usage = _aggregate_conversation_usage(conv_dir) if conv_dir is not None else {
        **{k: 0 for k in _USAGE_INT_FIELDS},
        "cost_usd": 0.0,
    }

    entry = {
        "analysed_at": analysis.get("analysed_at"),
        "git_ref": analysis.get("git_ref"),
        "coordinator_model": model,
        "conversation_id": analysis.get("conversation_id"),
        "date": analysis.get("date"),
        "requests": analysis.get("requests_analysed", 0),
        "steps": analysis.get("total_steps", 0),
        "avg_steps": analysis.get("avg_steps_per_request", 0),
        "findings_by_mode": dict(by_mode) if by_mode else {},
        "findings_by_severity": dict(by_severity) if by_severity else {},
        "finding_count": len(findings),
        "revoked_count": revoked_count,
        "revoked_by_mode": dict(revoked_by_mode) if revoked_by_mode else {},
        "usage": usage,
    }
    return json.dumps(entry, separators=(",", ":"))


def collect(quiet: bool = True) -> dict[str, int]:
    """Backfill metrics.jsonl from analysis.yaml files.

    Returns counts: ``{"new": int, "updated": int, "total": int}``.
    Suitable for both CLI invocation (via ``cmd_collect``) and
    in-process invocation from the agent's scheduled-loop wrapper.
    """
    analysis_files = find_analysis_files()
    if not analysis_files:
        if not quiet:
            print("No analysis.json files found.")
        return {"new": 0, "updated": 0, "total": 0}

    existing = load_existing_entries()
    new_count = 0
    updated_count = 0

    for path in analysis_files:
        analysis = _load_analysis(path)
        if analysis is None:
            continue

        key = f"{analysis.get('date')}:{analysis.get('conversation_id')}"
        model = _get_conversation_model(path.parent)
        new_entry = json.loads(extract_metrics_line(analysis, model=model, conv_dir=path.parent))

        if key in existing:
            old_entry = existing[key]
            if old_entry.get("analysed_at") == new_entry.get("analysed_at"):
                if not quiet:
                    print(f"  Already collected: {key}")
                continue
            existing[key] = new_entry
            updated_count += 1
            if not quiet:
                print(f"  Updated: {key}")
        else:
            existing[key] = new_entry
            new_count += 1
            if not quiet:
                findings = analysis.get("findings", [])
                print(f"  Collected: {key} ({len(findings)} findings)")

    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling .tmp then atomically replace, so a kill mid-
    # write leaves the previous metrics.jsonl intact rather than a
    # truncated file. ``Path.replace`` is atomic on POSIX and Windows.
    tmp = METRICS_FILE.with_suffix(METRICS_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for entry in existing.values():
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    tmp.replace(METRICS_FILE)

    if not quiet:
        parts = []
        if new_count:
            parts.append(f"{new_count} new")
        if updated_count:
            parts.append(f"{updated_count} updated")
        if not parts:
            parts.append("no changes")
        print(f"\n{', '.join(parts).capitalize()}. Total: {len(existing)} in metrics.jsonl")

    return {"new": new_count, "updated": updated_count, "total": len(existing)}


def cmd_collect(args: argparse.Namespace) -> None:
    collect(quiet=args.quiet)


# ── Summary ──────────────────────────────────────────────────────────────────


def load_metrics(
    after: str | None = None,
    before: str | None = None,
    ref: str | None = None,
) -> list[dict]:
    """Load and filter metrics entries."""
    if not METRICS_FILE.exists():
        return []

    entries = []
    for line in METRICS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        date = entry.get("date", "")
        if after and date < after:
            continue
        if before and date > before:
            continue
        if ref and entry.get("git_ref") != ref:
            continue
        entries.append(entry)

    return entries


def print_summary(entries: list[dict], label: str = "") -> None:
    """Print an aggregate summary of metrics entries."""
    if not entries:
        print(f"{'  ' if label else ''}No data.")
        return

    total_conversations = len(entries)
    total_requests = sum(e.get("requests", 0) for e in entries)
    total_steps = sum(e.get("steps", 0) for e in entries)
    total_findings = sum(e.get("finding_count", 0) for e in entries)
    total_revoked = sum(e.get("revoked_count", 0) for e in entries)
    conversations_with_issues = sum(1 for e in entries if e.get("finding_count", 0) > 0)

    avg_steps = total_steps / total_requests if total_requests else 0
    issue_pct = (conversations_with_issues / total_conversations * 100) if total_conversations else 0
    # Revocation rate measures "would-be findings that were retracted":
    # high rate signals the analyser is being trigger-happy and the
    # guide may need tightening for the offending failure modes.
    revocation_rate = (
        (total_revoked / (total_findings + total_revoked) * 100)
        if (total_findings + total_revoked) else 0
    )

    mode_totals: Counter = Counter()
    severity_totals: Counter = Counter()
    revoked_mode_totals: Counter = Counter()
    for e in entries:
        for mode, count in e.get("findings_by_mode", {}).items():
            mode_totals[mode] += count
        for sev, count in e.get("findings_by_severity", {}).items():
            severity_totals[sev] += count
        for mode, count in e.get("revoked_by_mode", {}).items():
            revoked_mode_totals[mode] += count

    dates = sorted(set(e.get("date", "") for e in entries))
    refs = sorted(set(e.get("git_ref", "") for e in entries if e.get("git_ref")))

    indent = "  " if label else ""

    if label:
        print(f"\n  ── {label} ──")

    print(f"{indent}Conversations: {total_conversations}  |  Requests: {total_requests}  |  Steps: {total_steps}")
    print(f"{indent}Avg steps/request: {avg_steps:.2f}")
    print(f"{indent}Findings: {total_findings}  |  Conversations with issues: {conversations_with_issues}/{total_conversations} ({issue_pct:.0f}%)")
    if total_revoked or total_findings:
        print(f"{indent}Revoked findings: {total_revoked}  |  Revocation rate: {revocation_rate:.1f}% ({total_revoked}/{total_findings + total_revoked})")

    if severity_totals:
        parts = [f"{sev}: {count}" for sev, count in sorted(severity_totals.items())]
        print(f"{indent}By severity: {', '.join(parts)}")

    if mode_totals or revoked_mode_totals:
        all_modes = sorted(set(mode_totals) | set(revoked_mode_totals))
        # Sort by total volume (emitted + revoked) descending so the
        # highest-pressure failure modes appear first.
        all_modes.sort(key=lambda m: -(mode_totals[m] + revoked_mode_totals[m]))
        print(f"{indent}By failure mode (emitted | revoked | revocation %):")
        for mode in all_modes:
            emitted = mode_totals[mode]
            revoked = revoked_mode_totals[mode]
            total = emitted + revoked
            rate = (revoked / total * 100) if total else 0
            print(f"{indent}  {mode}: {emitted} | {revoked} | {rate:.0f}%")

    if len(dates) > 1:
        print(f"{indent}Date range: {dates[0]} to {dates[-1]}")
    elif dates:
        print(f"{indent}Date: {dates[0]}")

    if refs:
        print(f"{indent}Git refs: {', '.join(refs)}")


def cmd_summary(args: argparse.Namespace) -> None:
    if args.compare:
        if len(args.compare) != 2:
            print("Error: --compare requires exactly two git refs")
            sys.exit(1)
        ref_a, ref_b = args.compare
        entries_a = load_metrics(after=args.after, before=args.before, ref=ref_a)
        entries_b = load_metrics(after=args.after, before=args.before, ref=ref_b)
        print(f"Comparing {ref_a} vs {ref_b}")
        print_summary(entries_a, label=ref_a)
        print_summary(entries_b, label=ref_b)

        # Delta
        def count_findings(entries: list[dict]) -> int:
            return sum(e.get("finding_count", 0) for e in entries)

        def count_revoked(entries: list[dict]) -> int:
            return sum(e.get("revoked_count", 0) for e in entries)

        def avg_steps(entries: list[dict]) -> float:
            total_r = sum(e.get("requests", 0) for e in entries)
            total_s = sum(e.get("steps", 0) for e in entries)
            return total_s / total_r if total_r else 0

        f_a, f_b = count_findings(entries_a), count_findings(entries_b)
        r_a, r_b = count_revoked(entries_a), count_revoked(entries_b)
        s_a, s_b = avg_steps(entries_a), avg_steps(entries_b)

        print(f"\n  ── Delta ({ref_a} → {ref_b}) ──")
        f_delta = f_b - f_a
        r_delta = r_b - r_a
        s_delta = s_b - s_a
        print(f"  Findings: {f_a} → {f_b} ({'+' if f_delta >= 0 else ''}{f_delta})")
        if r_a or r_b:
            print(f"  Revoked: {r_a} → {r_b} ({'+' if r_delta >= 0 else ''}{r_delta})")
        print(f"  Avg steps/request: {s_a:.2f} → {s_b:.2f} ({'+' if s_delta >= 0 else ''}{s_delta:.2f})")
    else:
        entries = load_metrics(after=args.after, before=args.before, ref=args.ref)
        print_summary(entries)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Metrics for passive conversation analysis")
    sub = parser.add_subparsers(dest="command")

    collect_p = sub.add_parser("collect", help="Backfill metrics.jsonl from analysis.json files")
    collect_p.add_argument("-q", "--quiet", action="store_true", help="Suppress per-file output")

    summary_p = sub.add_parser("summary", help="Show metrics summary")
    summary_p.add_argument("--after", help="Only include dates >= this (YYYY-MM-DD)")
    summary_p.add_argument("--before", help="Only include dates <= this (YYYY-MM-DD)")
    summary_p.add_argument("--ref", help="Filter to a specific git ref")
    summary_p.add_argument("--compare", nargs=2, metavar="REF", help="Compare two git refs")

    args = parser.parse_args()
    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "summary":
        cmd_summary(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
