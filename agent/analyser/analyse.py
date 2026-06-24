#!/usr/bin/env python3
"""Standalone conversation analyser using LiteLLM.

Scans for unanalysed conversations, sends logs + analysis guide to an LLM,
writes analysis.yaml and collects metrics. Batches multiple conversations per
API call to avoid resending the analysis guide each time.

Usage:
    python analyse.py                          # Scan once and exit
    python analyse.py --loop                   # Scan every 30 minutes (default)
    python analyse.py --loop --interval 15     # Scan every 15 minutes
    python analyse.py --conversation c01       # Analyse a specific conversation (today/yesterday)
    python analyse.py --conversation 2026-03-28/c01  # Analyse a specific date/conversation
    python analyse.py --staleness 30           # 30 min staleness threshold (default 60)
    python analyse.py --model anthropic/claude-opus-4-6  # Override model
    python analyse.py --batch-size 3           # Conversations per API call (default 5)

Pending feedback (feedback.yaml with lesson_status: pending) is processed
automatically at the start of every run, before analysis begins.

Environment:
    LLM_MODEL_ANALYSER         — model in provider/model format (no built-in default — must be set, or use --model)
    LLM_MODEL                  — fallback if LLM_MODEL_ANALYSER not set; usually the agent's primary model
    LLM_API_KEY_<PROVIDER>     — API key per provider (e.g. LLM_API_KEY_ANTHROPIC, LLM_API_KEY_OPENAI)
    ANALYSER_BATCH_SIZE        — default for --batch-size; shared with the on-demand scan path
    SWARPIUS_DATA_DIR            — override the default agent/data/ data root

Intentional duplication with the agent:
    This module re-implements .env parsing, API-key resolution,
    data-directory resolution, and model-profile lookup that also live in
    app. The duplication is deliberate so the analyser runs as a
    standalone process (own container, own requirements.txt) without
    importing the rest of app. The one exception is
    app.model_profiles — stdlib-only and safe to import — used to
    honour per-model tuning constraints (e.g. GPT-5's temperature=1.0
    requirement) when making LLM calls. Fixes that touch the shared
    surface (new env vars, model-profile changes) need to land in both
    places.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filelock import FileLock, Timeout

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from analyser import metrics as _metrics
from analyser.feedback import (
    LESSONS_HEADER,
    _parse_lessons,
    build_analyser_prompt,
    check_finding_resolved,
    count_lessons,
    read_feedback,
    read_lessons,
    write_feedback,
    write_lesson,
)
from analyser.llm_layer import (
    CompletionResult,
    litellm,
    llm_completion,
    parse_json_response,
)

SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_DIR = SCRIPT_DIR.parent
REPO_ROOT = AGENT_DIR.parent
GUIDE_PATH = SCRIPT_DIR / "analysis-guide.md"


class AnalyserFatalError(Exception):
    """Raised by helper functions on unrecoverable analyser errors
    (auth failure, permanent provider misconfig). main() catches it
    and translates to a non-zero exit code; the agent's in-process
    call path catches it and surfaces an error to the UI without
    taking the agent down.
    """


try:
    # Canonical, bundle-aware resolver — the analyser must read where the
    # request logger writes (the per-platform user-data dir in a bundle).
    from app.data_paths import data_dir as _data_dir
except Exception:
    # Fallback for running this module as a bare script (no app on path);
    # source-mode only, so the simple data dir is correct.
    def _data_dir() -> Path:
        raw = os.environ.get("SWARPIUS_DATA_DIR", "")
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else AGENT_DIR / p
        return AGENT_DIR / "data"


LOGS_ROOT = _data_dir() / "logs" / "conversation"
LESSONS_PATH = _data_dir() / "analysis" / "lessons-learned.md"

DEFAULT_STALENESS_MINUTES = 60
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_BATCH_SIZE = 5


def resolve_batch_size(default: int = DEFAULT_BATCH_SIZE) -> int:
    """Resolve the scan batch size from ``ANALYSER_BATCH_SIZE``.

    Read at call time, not import time, so tests and runtime env changes
    don't have to account for module-import-time state.  Falls back to
    ``default`` when unset, empty, non-numeric, or non-positive.
    """
    raw = os.environ.get("ANALYSER_BATCH_SIZE", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


ANALYSIS_FILENAME = "analysis.yaml"
SKIPPED_FILENAME = "analysis.skipped.yaml"
FEEDBACK_FILENAME = "feedback.yaml"
HISTORY_FILENAME = "analysis-history.yaml"
ANALYSIS_HISTORY_MAX_ENTRIES = int(os.environ.get("ANALYSIS_HISTORY_MAX_ENTRIES", "20"))
# How long a processing state can live before we consider it crashed
# and reset it to pending. A legitimate single-pass validation
# (lesson extract + one analyse_batch) takes ~tens of seconds — 10
# minutes is comfortable slack without leaving stuck items around
# forever.
STUCK_PROCESSING_MINUTES = 10

log = logging.getLogger("analyse")


# ── YAML helpers ─────────────────────────────────────────────────────────────


class _LiteralStr(str):
    """Marker for strings that should use YAML literal block style (|)."""


def _literal_representer(dumper: yaml.Dumper, data: _LiteralStr) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_LiteralStr, _literal_representer)


def _wrap_text(text: str, width: int = 100) -> str:
    """Word-wrap a string to the given width, preserving existing line breaks."""
    import textwrap

    wrapped_paragraphs = []
    for paragraph in text.split("\n"):
        if len(paragraph) <= width:
            wrapped_paragraphs.append(paragraph)
        else:
            wrapped_paragraphs.append(textwrap.fill(paragraph, width=width))
    return "\n".join(wrapped_paragraphs)


def _prepare_for_yaml(obj: dict) -> dict:
    """Convert long strings to word-wrapped literal block scalars for readable YAML."""
    result = {}
    for key, value in obj.items():
        if isinstance(value, str) and (len(value) > 80 or "\n" in value):
            result[key] = _LiteralStr(_wrap_text(value))
        elif isinstance(value, list):
            result[key] = [
                _prepare_for_yaml(item) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, dict):
            result[key] = _prepare_for_yaml(value)
        else:
            result[key] = value
    return result


# ── API key resolution ───────────────────────────────────────────────────────


def _load_env_file() -> dict[str, str]:
    """Load key=value pairs from agent/.env if present."""
    env_file = AGENT_DIR / ".env"
    if not env_file.exists():
        return {}
    result: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def resolve_model(model_arg: str) -> str | None:
    """Resolve the model spec from --model flag or env vars.

    Returns ``None`` if no model is configured anywhere — the caller
    surfaces this as an error. No hardcoded provider fallback:
    defaulting to a specific Anthropic model would force users on
    other providers to set the env var just to opt out.
    """
    if model_arg:
        return model_arg
    return os.environ.get("LLM_MODEL_ANALYSER") or os.environ.get("LLM_MODEL") or None


def _load_context_snapshot(conv_dir: Path) -> dict | None:
    """Read ``context_snapshot.json`` from a conversation directory.

    Written by the agent on the first request of each conversation;
    captures non-secret config as it was when the conversation ran
    (persona, default zone, coordinator model + profile tuning,
    registered skills).  Returns None when the file is absent or
    unreadable.
    """
    path = conv_dir / "context_snapshot.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _format_coordinator_config_block(snapshot: dict) -> str | None:
    """Format a snapshot dict into the 'Coordinator configuration' block.

    The block surfaces non-secret config the analyser otherwise cannot
    see — persona styling, default zone, coordinator model and tuning,
    registered skills — so findings can distinguish persona-driven
    language from confabulation, configured defaults from fabricated
    zones, tuned step budgets from the stock cap, and absent-skill
    scenarios from genuine skill-use failures.
    """
    lines: list[str] = []
    if snapshot.get("persona"):
        lines.append(f"- Persona: {snapshot['persona']}")
    if snapshot.get("default_zone"):
        lines.append(f"- Default Roon zone: {snapshot['default_zone']}")
    if snapshot.get("coordinator_model"):
        lines.append(f"- Coordinator model: {snapshot['coordinator_model']}")

    profile = snapshot.get("model_profile") or {}
    if "max_coordinator_steps" in profile:
        lines.append(f"- Max coordinator steps: {profile['max_coordinator_steps']}")
    if "temperature" in profile:
        lines.append(f"- Temperature: {profile['temperature']}")

    skills = snapshot.get("registered_skills") or []
    if skills:
        lines.append("- Registered skills (name — description):")
        for s in skills:
            name = s.get("name", "?")
            desc = (s.get("description") or "").strip().replace("\n", " ")
            lines.append(f"    - {name} — {desc}")

    if not lines:
        return None
    return "## Coordinator configuration\n\n" + "\n".join(lines)


def build_coordinator_config_block(conv_dir: Path) -> str | None:
    """Build the analyser's 'Coordinator configuration' block from
    ``context_snapshot.json`` in the conversation directory.

    Returns None if no snapshot exists (the analyser then runs without
    that block, which is fine — findings just won't surface
    persona/model context).
    """
    snapshot = _load_context_snapshot(conv_dir)
    if snapshot is None:
        return None
    return _format_coordinator_config_block(snapshot)


def _handle_llm_failure(completion: CompletionResult, context: str) -> None:
    """Log and (for permanent errors) halt on a failed LLM call.

    The API key is resolved once at startup, so a permanent error —
    auth, misconfig — will fail every subsequent call identically.
    Continuing to retry every scan interval is noise, not resilience:
    surface the underlying provider message and exit so the operator
    notices and can fix the key / model before restarting.
    """
    detail = f": {completion.detail}" if completion.detail else ""
    log.error(
        "LLM call failed (%s) for %s%s",
        completion.error_kind or "unknown",
        context,
        detail,
    )
    if completion.error_kind == "permanent":
        log.error(
            "Halting — permanent errors (auth, misconfig) won't recover by "
            "retrying. Fix the API key or model selection in agent/.env and "
            "restart the analyser.",
        )
        raise AnalyserFatalError(
            f"Permanent LLM failure for {context}: {completion.detail or 'no detail'}",
        )


def _mark_skipped(conv_dir: Path, completion: CompletionResult) -> None:
    """Record that a conversation cannot be analysed and must not be retried.

    Used for failures that will never succeed on a retry (e.g. the conversation
    is too large for the model). Writes a marker that ``find_eligible_conversations``
    honours, so one un-analysable conversation neither loops forever nor blocks
    the rest. Delete the marker to force a re-analysis."""
    label = f"{conv_dir.parent.name}/{conv_dir.name}"
    detail = completion.detail or "no detail"
    log.error(
        "Skipping %s — cannot be analysed (%s): %s. It will not be retried; "
        "remove %s to force a re-analysis.",
        label, completion.error_kind, detail, SKIPPED_FILENAME,
    )
    marker = {"skipped": True, "reason": completion.error_kind, "detail": completion.detail}
    (conv_dir / SKIPPED_FILENAME).write_text(
        yaml.safe_dump(marker, sort_keys=False), encoding="utf-8",
    )


def resolve_api_key(model: str) -> str:
    """Resolve API key for the given model's provider.

    Checks LLM_API_KEY_<PROVIDER> env vars first, then agent/.env.
    Local providers (e.g. ollama) don't need a key.
    """
    provider = model.split("/", 1)[0] if "/" in model else ""

    if provider in ("ollama", "ollama_chat"):
        return ""

    env_key = f"LLM_API_KEY_{provider.upper()}"
    api_key = os.environ.get(env_key, "").strip()
    if api_key:
        return api_key

    env_vars = _load_env_file()
    api_key = env_vars.get(env_key, "").strip()
    if api_key:
        return api_key

    log.error("No API key found for provider '%s'. Set %s in your environment or in agent/.env", provider, env_key)
    raise AnalyserFatalError(
        f"No API key for provider '{provider}'. Set {env_key}.",
    )


# ── Git ref ──────────────────────────────────────────────────────────────────


def get_git_ref() -> str | None:
    """Get short git HEAD ref, or None if not a git repo / in a bundle."""
    if getattr(sys, "frozen", False):
        # A bundle isn't a git repo, and invoking git there pops the macOS
        # "install command line tools" dialog. Skip it.
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT),
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


# ── Conversation discovery ───────────────────────────────────────────────────


def get_last_request_time(conv_dir: Path) -> datetime | None:
    """Find the timestamp of the most recent request in a conversation."""
    latest = None
    for req_dir in conv_dir.iterdir():
        if not req_dir.is_dir() or not req_dir.name.startswith("rq-"):
            continue
        request_file = req_dir / "request.json"
        if not request_file.exists():
            continue
        try:
            data = json.loads(request_file.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(data["timestamp"])
            if latest is None or ts > latest:
                latest = ts
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return latest


def find_eligible_conversations(staleness_minutes: int) -> list[Path]:
    """Find conversation directories that are stale and unanalysed.

    Logs the scanned root and a per-reason skip breakdown (already-analysed,
    no readable request.json, not-yet-stale) so the eligibility outcome is
    self-explanatory in the logs.
    """
    if not LOGS_ROOT.is_dir():
        log.info("Scan eligibility: logs root does not exist: %s", LOGS_ROOT)
        return []

    cutoff = datetime.now() - timedelta(minutes=staleness_minutes)
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    eligible: list[Path] = []
    considered = 0
    skipped = {"already_analysed": 0, "no_request_time": 0, "not_stale": 0, "unanalysable": 0}

    for date_str in [today, yesterday]:
        date_dir = LOGS_ROOT / date_str
        if not date_dir.is_dir():
            continue
        for conv_dir in sorted(date_dir.iterdir()):
            if not conv_dir.is_dir() or not conv_dir.name.startswith("c"):
                continue
            considered += 1
            if (conv_dir / ANALYSIS_FILENAME).exists():
                skipped["already_analysed"] += 1
                continue
            if (conv_dir / SKIPPED_FILENAME).exists():
                skipped["unanalysable"] += 1
                continue
            last_time = get_last_request_time(conv_dir)
            if last_time is None:
                skipped["no_request_time"] += 1
                log.debug(
                    "Scan eligibility: skip %s/%s — no readable request.json",
                    date_str, conv_dir.name,
                )
                continue
            if last_time < cutoff:
                eligible.append(conv_dir)
            else:
                skipped["not_stale"] += 1
                log.debug(
                    "Scan eligibility: skip %s/%s — last request %s not older than "
                    "cutoff %s (staleness %dm)",
                    date_str, conv_dir.name, last_time.isoformat(),
                    cutoff.isoformat(), staleness_minutes,
                )

    log.info(
        "Scan eligibility under %s (today+yesterday): %d eligible of %d conversation(s) "
        "— skipped %d already-analysed, %d no-request-time, %d not-stale, %d unanalysable",
        LOGS_ROOT, len(eligible), considered,
        skipped["already_analysed"], skipped["no_request_time"], skipped["not_stale"],
        skipped["unanalysable"],
    )
    return eligible


def resolve_conversation_path(spec: str) -> Path | None:
    """Resolve a conversation spec like 'c01' or '2026-03-28/c01' to a path."""
    if "/" in spec:
        path = LOGS_ROOT / spec
        return path if path.is_dir() else None

    # Search today, then yesterday
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    for date_str in [today, yesterday]:
        path = LOGS_ROOT / date_str / spec
        if path.is_dir():
            return path
    return None


# ── Log formatting ───────────────────────────────────────────────────────────


def _format_coordinator_prompt(system_prompt: str) -> str:
    """Format the coordinator system prompt for inclusion in the analyser
    payload. Returns the prompt verbatim, with internal ``## `` headings
    demoted to ``##### `` so they nest cleanly under the payload's
    section structure instead of clashing with the payload's own
    top-level headers."""
    return re.sub(r"^## ", "##### ", system_prompt, flags=re.MULTILINE)


def format_conversation_logs(conv_dir: Path) -> str:
    """Read and format all logs in a conversation for the API call."""
    date_str = conv_dir.parent.name
    conv_id = conv_dir.name
    parts = [f"## Conversation: {conv_id} ({date_str})\n"]

    config_block = build_coordinator_config_block(conv_dir)
    if config_block:
        parts.append(config_block)
        parts.append("")

    summary_file = conv_dir / "conversation_summary.json"
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            parts.append(f"Topic: {summary.get('topic_summary', 'unknown')}\n")
        except (json.JSONDecodeError, OSError):
            # Corrupt summary — render the report without the topic
            # line rather than block the whole analysis.
            pass

    req_dirs = sorted(
        [d for d in conv_dir.iterdir() if d.is_dir() and d.name.startswith("rq-")],
        key=lambda d: d.name,
    )

    for req_dir in req_dirs:
        parts.append(f"\n### Request: {req_dir.name}\n")

        request_file = req_dir / "request.json"
        if request_file.exists():
            try:
                data = json.loads(request_file.read_text(encoding="utf-8"))
                parts.append(f"User input: {json.dumps(data.get('user_input', ''))}")
                parts.append(f"Timestamp: {data.get('timestamp', 'unknown')}\n")
            except (json.JSONDecodeError, OSError):
                parts.append("(request.json unreadable)\n")

        prompt_file = req_dir / "prompts" / "coordinator_system.txt"
        if prompt_file.exists():
            try:
                prompt_text = prompt_file.read_text(encoding="utf-8")
                parts.append("#### Coordinator System Prompt (full, exactly as the coordinator saw it):\n")
                parts.append(_format_coordinator_prompt(prompt_text))
                parts.append("")
            except OSError:
                # Unreadable prompt file — omit the section rather
                # than break the rest of the request rendering.
                pass

        tool_dir = req_dir / "tool_executions"
        if tool_dir.is_dir():
            tool_files = sorted(tool_dir.glob("*.json"))
            if tool_files:
                parts.append("#### Tool Executions:\n")
                for tf in tool_files:
                    try:
                        td = json.loads(tf.read_text(encoding="utf-8"))
                        parts.append(f"**{tf.name}** (step {td.get('step', '?')}, {td.get('selected_skill', '?')}):")
                        parts.append(f"  Input: {json.dumps(td.get('tool_input', {}), separators=(',', ':'))}")
                        # Compact the output — it can be large
                        output = td.get("tool_output", {})
                        output_str = json.dumps(output, separators=(",", ":"))
                        if len(output_str) > 2000:
                            output_str = output_str[:2000] + "...(truncated)"
                        parts.append(f"  Output: {output_str}")
                        if td.get("error"):
                            parts.append(f"  Error: {td['error']}")
                        parts.append(f"  Duration: {td.get('duration_ms', '?')}ms\n")
                    except (json.JSONDecodeError, OSError):
                        parts.append(f"  ({tf.name} unreadable)\n")

        outcome_file = req_dir / "outcome.json"
        if outcome_file.exists():
            try:
                od = json.loads(outcome_file.read_text(encoding="utf-8"))
                parts.append("#### Outcome:")
                parts.append(f"Status: {od.get('status', 'unknown')}")
                parts.append(f"Total steps: {od.get('total_steps', '?')}")
                parts.append(f"Duration: {od.get('total_duration_ms', '?')}ms")
                response = od.get("chat_response", "")
                if response:
                    # Truncate very long responses
                    if len(response) > 1500:
                        response = response[:1500] + "...(truncated)"
                    parts.append(f"Response: {json.dumps(response)}")
                detailed = od.get("detailed_information")
                if detailed:
                    if len(detailed) > 3000:
                        detailed = detailed[:3000] + "...(truncated)"
                    parts.append(f"Detailed information (user-visible collapsible sections): {json.dumps(detailed)}")
                if od.get("problem_description"):
                    parts.append(f"Problem: {od['problem_description']}")
                parts.append("")
            except (json.JSONDecodeError, OSError):
                parts.append("(outcome.json unreadable)\n")

        parts.append("---\n")

    return "\n".join(parts)


# ── Analysis ─────────────────────────────────────────────────────────────────


def analyse_batch(
    model: str,
    api_key: str,
    conv_dirs: list[Path],
    guide_text: str,
    git_ref: str | None,
) -> list[dict | None]:
    """Analyse one or more conversations in a single API call."""
    labels = [f"{d.parent.name}/{d.name}" for d in conv_dirs]

    if len(conv_dirs) == 1:
        log.info("Analysing %s ...", labels[0])
    else:
        log.info("Analysing batch of %d: %s", len(conv_dirs), ", ".join(labels))

    sections = []
    for conv_dir in conv_dirs:
        sections.append(format_conversation_logs(conv_dir))

    all_logs = "\n\n".join(sections)

    if len(conv_dirs) == 1:
        instruction = "Analyse this conversation. Produce a single JSON object following the schema in the analysis guide."
    else:
        instruction = (
            f"Analyse these {len(conv_dirs)} conversations independently. "
            "Produce a JSON array containing one analysis object per conversation, "
            "in the same order as presented. Each object follows the schema in the analysis guide."
        )

    user_message = f"""{instruction} The current git ref is: {git_ref or 'unknown'}

{all_logs}

No markdown wrapping — output raw JSON only."""

    system_prompt = build_analyser_prompt(guide_text, LESSONS_PATH)

    completion = llm_completion(
        model, api_key, system_prompt, user_message,
        max_tokens=4096 * len(conv_dirs),
    )
    if completion.text is None:
        _handle_llm_failure(completion, ", ".join(labels))  # halts on permanent
        # A single conversation that is too large will never succeed — mark it
        # skipped so it is not retried every scan. In a multi-conversation batch
        # the size may be the combination, so leave it to the caller to retry
        # each one alone (where a genuinely-too-large one gets marked here).
        if completion.error_kind == "input_shape" and len(conv_dirs) == 1:
            _mark_skipped(conv_dirs[0], completion)
        return [None] * len(conv_dirs)

    parsed = parse_json_response(completion.text)

    if parsed is None:
        log.error("Failed to parse JSON from response")
        log.debug("Raw response: %s", completion.text[:500])
        return [None] * len(conv_dirs)

    matched = _match_parsed_analyses(parsed, conv_dirs)
    for analysis in matched:
        if analysis is not None:
            removed = _apply_revocations(analysis)
            if removed:
                log.info(
                    "Applied %d revocation(s) for %s",
                    removed, analysis.get("conversation_id", "?"),
                )
    return matched


def _match_parsed_analyses(
    parsed: dict | list,
    conv_dirs: list[Path],
) -> list[dict | None]:
    """Pair an LLM batch response with its conversation dirs.

    Always matches on ``(conversation_id, date)`` rather than positional
    order — an LLM can return the right count but in a different order,
    which would silently write analyses to the wrong directories.
    Unidentified or extra items are dropped; unmatched positions yield
    None. A bare dict is accepted as a one-item list.
    """
    if isinstance(parsed, dict):
        items: list = [parsed]
    elif isinstance(parsed, list):
        items = parsed
    else:
        log.error("Expected JSON object or array, got %s", type(parsed).__name__)
        return [None] * len(conv_dirs)

    if len(items) != len(conv_dirs):
        log.warning(
            "LLM response has %d items, expected %d. Matching by conversation_id.",
            len(items), len(conv_dirs),
        )

    result: list[dict | None] = [None] * len(conv_dirs)
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("conversation_id", "")
        date = item.get("date", "")
        for i, conv_dir in enumerate(conv_dirs):
            if (
                conv_dir.name == cid
                and conv_dir.parent.name == date
                and result[i] is None
            ):
                result[i] = item
                break
    return result


def _is_within_edit_distance_1(a: str, b: str) -> bool:
    """True iff the Levenshtein distance between ``a`` and ``b`` is at
    most 1 (handles single substitution, insertion, or deletion).
    Used to tolerate the occasional character typo when the analyser
    references a finding id in ``revoked_findings``."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    longer, shorter = (a, b) if la > lb else (b, a)
    return any(
        longer[:i] + longer[i + 1:] == shorter
        for i in range(len(longer))
    )


def _apply_revocations(analysis: dict) -> int:
    """Apply ``revoked_findings`` to ``findings`` in place.

    Each revoked entry references a finding ``id`` plus a ``reason``.
    Matches by exact id first; falls back to a distance-1 fuzzy match
    only when exactly one candidate qualifies (ambiguous matches are
    skipped to avoid revoking the wrong finding).

    Matched revocations are enriched with ``original_finding`` (the full
    finding dict before removal) so metrics and the frontend can read
    the original failure_mode, severity, summary, etc. without having
    to cross-reference back to ``findings`` (which by then is filtered).

    Returns the number of findings actually removed.
    """
    revoked = analysis.get("revoked_findings") or []
    findings = analysis.get("findings") or []
    if not isinstance(revoked, list) or not isinstance(findings, list):
        return 0
    if not revoked or not findings:
        return 0

    finding_ids_to_index: dict[str, int] = {}
    for i, f in enumerate(findings):
        if isinstance(f, dict):
            fid = f.get("id")
            if isinstance(fid, str) and fid:
                finding_ids_to_index[fid] = i

    matches: list[tuple[int, int]] = []  # (revocation_index, finding_index)
    for ri, r in enumerate(revoked):
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        if rid in finding_ids_to_index:
            matches.append((ri, finding_ids_to_index[rid]))
            continue
        candidates = [
            (fid, idx) for fid, idx in finding_ids_to_index.items()
            if _is_within_edit_distance_1(rid, fid)
        ]
        if len(candidates) == 1:
            fid, idx = candidates[0]
            matches.append((ri, idx))
            log.info("Fuzzy-matched revocation %r -> finding %r", rid, fid)
        elif len(candidates) > 1:
            log.warning(
                "Revocation %r ambiguous (matches %s); skipping",
                rid, [c[0] for c in candidates],
            )

    if not matches:
        return 0

    to_remove: set[int] = set()
    for ri, fi in matches:
        revoked[ri]["original_finding"] = findings[fi]
        to_remove.add(fi)

    analysis["findings"] = [
        f for i, f in enumerate(findings) if i not in to_remove
    ]
    return len(to_remove)


def _get_existing_git_ref(conv_dir: Path) -> str | None:
    """Read git_ref from existing analysis.yaml, if present."""
    path = conv_dir / ANALYSIS_FILENAME
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("git_ref") if isinstance(data, dict) else None
    except Exception:
        log.debug("Failed to read git_ref from %s", path, exc_info=True)
        return None


def _build_history_entry(conv_dir: Path) -> dict | None:
    """Read the current analysis.yaml + feedback.yaml and build a
    history entry snapshotting them.

    Returns None when there's no existing analysis to snapshot, or when
    analysis.yaml is corrupt/unreadable (leaves the file in place for
    operator investigation).
    """
    analysis_path = conv_dir / ANALYSIS_FILENAME
    if not analysis_path.exists():
        return None
    try:
        existing = yaml.safe_load(analysis_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            return None
    except (OSError, yaml.YAMLError):
        log.warning(
            "Failed to read existing analysis for snapshot: %s",
            analysis_path, exc_info=True,
        )
        return None

    existing["feedback"] = read_feedback(conv_dir)
    existing["superseded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return existing


def _read_history(history_path: Path) -> list:
    """Read analysis-history.yaml, returning [] if missing/corrupt."""
    if not history_path.exists():
        return []
    try:
        data = yaml.safe_load(history_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        log.warning("Failed to parse %s — starting fresh", history_path, exc_info=True)
        return []
    if not isinstance(data, list):
        log.warning("Corrupt history in %s — starting fresh", history_path)
        return []
    return data


def write_analysis(conv_dir: Path, analysis: dict) -> None:
    """Write analysis.yaml with atomic snapshot-and-clear semantics.

    Flow (all-or-nothing with respect to observable filesystem state):

      1. Build the history entry in memory (old analysis + current feedback).
      2. Build new analysis content in memory.
      3. Write new analysis to ``analysis.yaml.tmp`` (atomic-write step 1).
      4. Write updated history to ``analysis-history.yaml.tmp`` (atomic
         step 2) IF there was a prior analysis to snapshot.
      5. Atomically rename new analysis → ``analysis.yaml``
         (COMMIT POINT — new analysis is now visible).
      6. Atomically rename history → ``analysis-history.yaml``.
      7. Delete ``feedback.yaml`` (last, so a failure before this point
         leaves feedback intact and re-processing is idempotent).

    Failure handling:
      - Failure before step 5 → temp files removed, on-disk state unchanged.
      - Failure between step 5 and step 7 → feedback is preserved in
        feedback.yaml (invariant A). Next scan re-processes it; lesson
        writes are (heading, source)-keyed so they don't duplicate.

    Preserves the git_ref from any existing analysis.yaml since the
    ref should reflect when the conversation happened, not when
    re-analysis ran.
    """
    analysis_path = conv_dir / ANALYSIS_FILENAME
    history_path = conv_dir / HISTORY_FILENAME
    feedback_path = conv_dir / FEEDBACK_FILENAME
    temp_analysis = conv_dir / (ANALYSIS_FILENAME + ".tmp")
    temp_history = conv_dir / (HISTORY_FILENAME + ".tmp")

    # Step 1: build history entry (captures current analysis + feedback)
    history_entry = _build_history_entry(conv_dir)

    # Step 2: build new analysis content
    existing_ref = _get_existing_git_ref(conv_dir)
    if existing_ref:
        analysis["git_ref"] = existing_ref
    yaml_data = _prepare_for_yaml(analysis)
    new_content = yaml.dump(
        yaml_data, default_flow_style=False, sort_keys=False,
        allow_unicode=True, width=120,
    )

    # Steps 3–4: write temp files. If either raises, clean up and bail.
    try:
        temp_analysis.write_text(new_content, encoding="utf-8")
        if history_entry is not None:
            history = _read_history(history_path)
            history.append(history_entry)
            # Rotate: keep only the last N entries so the file doesn't
            # grow linearly with every re-analysis.
            if len(history) > ANALYSIS_HISTORY_MAX_ENTRIES:
                history = history[-ANALYSIS_HISTORY_MAX_ENTRIES:]
            temp_history.write_text(
                yaml.dump(
                    history, default_flow_style=False, sort_keys=False,
                    allow_unicode=True, width=120,
                ),
                encoding="utf-8",
            )
    except OSError:
        _unlink_if_exists(temp_analysis)
        _unlink_if_exists(temp_history)
        raise

    # Step 5: COMMIT POINT — new analysis becomes visible.
    temp_analysis.replace(analysis_path)

    # Step 6: publish history (if any).
    if history_entry is not None:
        temp_history.replace(history_path)

    # Step 7: delete feedback.yaml last. If this fails the next scan sees
    # feedback.yaml still there and re-processes (idempotent thanks to
    # lesson_status filtering + (heading, source) lesson dedup).
    if history_entry is not None and feedback_path.exists():
        feedback_path.unlink()

    log.info("Wrote %s", analysis_path)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        # Race-free "delete if present" — file already gone, success.
        pass
    except OSError:
        log.warning("Failed to clean up temp file %s", path, exc_info=True)


def collect_metrics() -> None:
    """Update metrics.jsonl from the latest analysis.yaml files.

    Metrics are observability, not correctness — a failure here
    shouldn't fail the analysis run, but the operator needs a loud
    log so stale metrics don't go unnoticed.
    """
    try:
        _metrics.collect(quiet=True)
    except Exception as exc:
        log.warning("Metrics collection failed: %s", exc)


# ── Feedback processing ──────────────────────────────────────────────────────

FEEDBACK_EXTRACT_PROMPT = """\
You are refining your own analytical capabilities based on operator feedback.

An operator has disputed one of your analysis findings. Your task: read the
original conversation logs, the finding you produced, and the operator's
rebuttal. Extract the general lesson — what domain knowledge were you missing
that caused the incorrect finding?

Write the lesson as analytical guidance for your future self:
- Frame it as general knowledge, not a rule about this specific case
- Explain what to consider, not what to always/never do
- Include enough context for the lesson to apply to novel situations
- Be concise — 2-4 sentences of guidance

IMPORTANT: If an "Existing lessons" section is provided, check whether any
existing lesson already covers the same concept. If so, UPDATE that lesson
by using its exact heading in the "heading" field and writing a refined body
that incorporates both the existing knowledge and the new feedback. Do not
create a new lesson that overlaps with an existing one. Only use a new heading
when the feedback teaches something genuinely distinct from all existing lessons.

Return a JSON object with exactly these fields:
{
  "heading": "<exact existing heading to update, OR a new 2-5 word heading>",
  "body": "<analytical guidance text — merged with existing if updating>",
  "source": "<request_id (date), FM-XX disposition>"
}

No markdown wrapping — output raw JSON only."""

LESSONS_CONSOLIDATE_PROMPT = """\
You are consolidating a set of analytical lessons learned from operator feedback.

Over time, lessons accumulate and may overlap, repeat the same concept with
different wording, or become unnecessarily verbose. Your task: consolidate
them into the minimal set of distinct, concise lessons.

Rules:
- Merge lessons that cover the same concept into one
- Preserve all source attributions (combine them if merging)
- Keep each lesson body to 2-4 sentences — trim verbosity
- Do not lose any knowledge — every insight must survive consolidation
- Do not invent new knowledge — only consolidate what exists

Return a JSON array of lesson objects:
[
  {
    "heading": "<2-5 word topic heading>",
    "body": "<concise analytical guidance>",
    "source": "<combined source attributions>"
  }
]

No markdown wrapping — output raw JSON only."""

LESSONS_CONSOLIDATE_THRESHOLD = 5


def consolidate_lessons(model: str, api_key: str) -> bool:
    """Consolidate lessons-learned.md if the count exceeds the threshold.

    Returns True if consolidation was performed, False otherwise.
    """
    current_count = count_lessons(LESSONS_PATH)
    if current_count < LESSONS_CONSOLIDATE_THRESHOLD:
        return False

    lessons_text = read_lessons(LESSONS_PATH)
    if not lessons_text.strip():
        return False

    log.info("Consolidating %d lessons ...", current_count)
    completion = llm_completion(
        model, api_key, LESSONS_CONSOLIDATE_PROMPT, lessons_text, max_tokens=2048,
    )
    if completion.text is None:
        _handle_llm_failure(completion, "lesson consolidation")
        return False

    consolidated = parse_json_response(completion.text)
    if not isinstance(consolidated, list) or not consolidated:
        log.warning("Lesson consolidation failed — unexpected response format")
        return False

    # Audit: log any input lesson whose heading doesn't appear in the
    # consolidated output. "Merged into another" is OK, but a silent
    # drop masks information loss.
    input_lessons = _parse_lessons(lessons_text)
    consolidated_headings = {
        str(lesson.get("heading", "")).strip().lower()
        for lesson in consolidated
    }
    for input_lesson in input_lessons:
        heading = input_lesson.get("heading", "").strip()
        if heading.lower() not in consolidated_headings:
            log.warning(
                "Lesson consolidation dropped heading: %r (source: %s)",
                heading, input_lesson.get("source", "unknown"),
            )

    # Rebuild lessons-learned.md from consolidated list
    parts = [LESSONS_HEADER.rstrip()]
    for lesson in consolidated:
        heading = lesson.get("heading", "Untitled")
        body = lesson.get("body", "").strip()
        source = lesson.get("source", "")
        parts.append(f"\n## {heading}\n")
        parts.append(body)
        if source:
            parts.append(f"\n*Source: {source}*")

    LESSONS_PATH.write_text("\n".join(parts) + "\n", encoding="utf-8")
    log.info("Consolidated %d lessons → %d", current_count, len(consolidated))
    return True


def find_pending_feedback() -> list[tuple[Path, str, str]]:
    """Scan conversation logs for feedback.yaml files with pending items.

    Returns a list of (conv_dir, request_id, failure_mode) tuples to
    process. Identity is (request_id, failure_mode) so it stays stable
    across re-analyses — positional indices would shift.
    """
    if not LOGS_ROOT.is_dir():
        return []

    pending: list[tuple[Path, str, str]] = []
    for date_dir in sorted(LOGS_ROOT.iterdir()):
        if not date_dir.is_dir():
            continue
        for conv_dir in sorted(date_dir.iterdir()):
            if not conv_dir.is_dir():
                continue
            fb_path = conv_dir / FEEDBACK_FILENAME
            if not fb_path.exists():
                continue
            items = read_feedback(conv_dir)
            for item in items:
                if item.get("lesson_status") != "pending":
                    continue
                request_id = item.get("request_id")
                failure_mode = item.get("failure_mode")
                if not request_id or not failure_mode:
                    log.warning(
                        "Skipping feedback item in %s without identity "
                        "(request_id/failure_mode missing)",
                        fb_path,
                    )
                    continue
                pending.append((conv_dir, request_id, failure_mode))
    return pending


def recover_stuck_processing() -> int:
    """Reset feedback items stuck in 'processing' state back to pending.

    A previous analyser run can die mid-processing (crash, sigkill, OOM)
    and leave a feedback entry with lesson_status='processing' and no
    one working on it. Called under the scan lock at the start of each
    scan cycle — so any 'processing' items at that point are either (a)
    genuinely in-flight from a concurrent writer (impossible inside the
    scan lock) or (b) stranded from a crashed previous run.

    Uses a staleness threshold (STUCK_PROCESSING_MINUTES) to avoid
    resetting items that were legitimately written very recently (e.g.
    by a queued run that just grabbed the lock before this sweep).

    Returns the number of items reset.
    """
    if not LOGS_ROOT.is_dir():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROCESSING_MINUTES)
    reset = 0
    for date_dir in sorted(LOGS_ROOT.iterdir()):
        if not date_dir.is_dir():
            continue
        for conv_dir in sorted(date_dir.iterdir()):
            if not conv_dir.is_dir():
                continue
            items = read_feedback(conv_dir)
            if not items:
                continue
            dirty = False
            for item in items:
                if item.get("lesson_status") != "processing":
                    continue
                started_raw = item.get("processing_started_at")
                if started_raw:
                    try:
                        started = datetime.fromisoformat(started_raw)
                    except ValueError:
                        started = None
                else:
                    started = None
                # Treat items without a timestamp as stuck — they came
                # from a writer that didn't persist the marker, which
                # shouldn't happen but we should recover rather than
                # leave them jammed.
                if started is None or started < cutoff:
                    log.warning(
                        "Resetting stuck processing state on %s/%s %s/%s "
                        "(started %s)",
                        date_dir.name, conv_dir.name,
                        item.get("request_id"), item.get("failure_mode"),
                        started_raw or "never",
                    )
                    item["lesson_status"] = "pending"
                    item.pop("processing_started_at", None)
                    reset += 1
                    dirty = True
            if dirty:
                write_feedback(conv_dir, items)
    return reset


def process_all_pending_feedback(
    model: str,
    api_key: str,
    guide_text: str,
    git_ref: str | None,
) -> int:
    """Find and process all pending feedback items. Returns count processed."""
    recover_stuck_processing()
    pending = find_pending_feedback()
    if not pending:
        return 0

    log.info("Found %d pending feedback item(s).", len(pending))
    processed = 0
    for conv_dir, request_id, failure_mode in pending:
        label = f"{conv_dir.parent.name}/{conv_dir.name}"
        identity = f"{request_id}/{failure_mode}"
        result = process_feedback(
            model, api_key, conv_dir, request_id, failure_mode, guide_text, git_ref,
        )
        if result.get("ok"):
            log.info("  %s %s: %s", label, identity, result["lesson_status"])
            processed += 1
        else:
            log.warning("  %s %s: failed — %s", label, identity, result.get("error"))

    return processed


def process_pending_feedback_for(
    conv_dir: Path,
    model: str,
    api_key: str,
    guide_text: str,
    git_ref: str | None,
) -> bool:
    """Process pending feedback items for a single conversation only.

    Used by the manual ``--conversation`` Re-Analyse path so clicking
    Re-Analyse on c10 does not consume LLM calls processing pending
    feedback on c5, c7, etc. The scheduled scanner still uses
    ``process_all_pending_feedback`` to sweep everything on its tick.

    ``recover_stuck_processing`` is still a global sweep — same as the
    scheduled path — because resetting stranded items elsewhere costs
    nothing (it's a file scan, no LLM) and keeps the recovery contract
    consistent across entrypoints.

    Returns True if any item's ``process_feedback`` call wrote a fresh
    ``analysis.yaml`` for ``conv_dir`` (validated, or best_effort with
    a successful re-analysis). The caller can use this to skip a
    redundant explicit ``analyse_batch`` afterwards.
    """
    recover_stuck_processing()
    items = read_feedback(conv_dir)
    pending = [
        (item["request_id"], item["failure_mode"])
        for item in items
        if item.get("lesson_status") == "pending"
        and item.get("request_id") and item.get("failure_mode")
    ]
    if not pending:
        return False

    label = f"{conv_dir.parent.name}/{conv_dir.name}"
    log.info("Found %d pending feedback item(s) on %s.", len(pending), label)
    wrote = False
    for request_id, failure_mode in pending:
        identity = f"{request_id}/{failure_mode}"
        result = process_feedback(
            model, api_key, conv_dir, request_id, failure_mode, guide_text, git_ref,
        )
        if result.get("wrote_analysis"):
            wrote = True
        if result.get("ok"):
            log.info("  %s %s: %s", label, identity, result.get("lesson_status"))
        else:
            log.warning("  %s %s: failed — %s", label, identity, result.get("error"))

    return wrote


def process_feedback(
    model: str,
    api_key: str,
    conv_dir: Path,
    request_id: str,
    failure_mode: str,
    guide_text: str,
    git_ref: str | None,
) -> dict:
    """Process operator feedback on a finding: extract lesson, validate, iterate.

    Findings are identified by ``(request_id, failure_mode)`` — the same
    stable key check_finding_resolved uses. When the identity has no
    match in the current analysis.yaml (can happen if a crash left
    feedback.yaml in place after a supersede), the feedback entry is
    marked ``lesson_status: orphaned`` and skipped without an LLM call.

    Returns a status dict with lesson_status and validation_iterations.
    """
    label = f"{conv_dir.parent.name}/{conv_dir.name}"
    identity = f"{request_id}/{failure_mode}"

    analysis_path = conv_dir / ANALYSIS_FILENAME
    if not analysis_path.exists():
        return {"ok": False, "error": f"No analysis.yaml in {label}"}

    analysis = yaml.safe_load(analysis_path.read_text(encoding="utf-8"))
    findings = analysis.get("findings", [])
    feedback_items = read_feedback(conv_dir)

    fb_item = None
    fb_idx = None
    for i, item in enumerate(feedback_items):
        if (item.get("request_id") == request_id
                and item.get("failure_mode") == failure_mode):
            fb_item = item
            fb_idx = i
            break

    if fb_item is None:
        return {"ok": False, "error": f"No feedback found for identity {identity}"}

    if fb_item.get("lesson_status") in ("validated", "best_effort"):
        log.info("Feedback for %s already processed (%s). Skipping.",
                 identity, fb_item["lesson_status"])
        return {
            "ok": True,
            "lesson_status": fb_item["lesson_status"],
            "validation_iterations": fb_item.get("validation_iterations", 0),
            "already_processed": True,
            "wrote_analysis": False,
        }

    original_finding = None
    for f in findings:
        if (f.get("request_id") == request_id
                and f.get("failure_mode") == failure_mode):
            original_finding = f
            break

    if original_finding is None:
        # The feedback references a finding that no longer exists in the
        # current analysis — partial-failure scenario where feedback.yaml
        # survived a supersede. Mark orphaned so we don't retry; the
        # lesson (if any) won't fire because there's nothing to validate
        # against.
        log.warning(
            "Feedback for %s in %s references a finding not present in "
            "the current analysis — marking orphaned.",
            identity, label,
        )
        fb_item["lesson_status"] = "orphaned"
        feedback_items[fb_idx] = fb_item
        write_feedback(conv_dir, feedback_items)
        return {
            "ok": True,
            "lesson_status": "orphaned",
            "validation_iterations": 0,
            "wrote_analysis": False,
        }

    disposition = fb_item["disposition"]

    # Transition to 'processing' and commit to disk before any LLM work.
    # The UI polls feedback status and uses this to lock the dispute
    # form — so the transition must be on disk before we start consuming
    # time with LLM calls. Persists across browser refresh.
    fb_item["lesson_status"] = "processing"
    fb_item["processing_started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    feedback_items[fb_idx] = fb_item
    write_feedback(conv_dir, feedback_items)

    log.info("Processing feedback for %s %s (%s) ...",
             label, identity, disposition)

    conversation_logs = format_conversation_logs(conv_dir)
    lesson_json = _extract_lesson(
        model, api_key, conversation_logs, original_finding, fb_item,
    )
    if lesson_json is None:
        fb_item["lesson_status"] = "error"
        feedback_items[fb_idx] = fb_item
        write_feedback(conv_dir, feedback_items)
        return {
            "ok": False,
            "error": "Failed to extract lesson from API response",
            "wrote_analysis": False,
        }

    # Single-pass validation: write the lesson, re-analyse once, see
    # whether the finding is resolved. With temperature=0 pinned for
    # the analyser there's nothing to gain from a second attempt of
    # the same lesson; the previous refinement loop also pushed
    # lessons toward overfitting (more specific → more aggressive at
    # making the disputed finding disappear). best_effort is the
    # honest terminal state when the lesson doesn't resolve the
    # finding — the lesson is preserved in lessons-learned.md and
    # influences future analyses regardless.
    write_lesson(
        LESSONS_PATH,
        heading=lesson_json["heading"],
        body=lesson_json["body"],
        source=lesson_json["source"],
    )

    results = analyse_batch(model, api_key, [conv_dir], guide_text, git_ref)
    new_analysis = results[0] if results else None
    if new_analysis is None:
        log.warning("Re-analysis failed; lesson saved without validation.")
        fb_item["lesson_status"] = "best_effort"
        fb_item["validation_iterations"] = 1
        feedback_items[fb_idx] = fb_item
        write_feedback(conv_dir, feedback_items)
        return {
            "ok": True,
            "lesson_status": "best_effort",
            "validation_iterations": 1,
            "wrote_analysis": False,
        }

    status = check_finding_resolved(original_finding, new_analysis, disposition)
    log.info("Validation result: %s", status)

    final_status = "validated" if status == "validated" else "best_effort"
    fb_item["lesson_status"] = final_status
    fb_item["validation_iterations"] = 1
    feedback_items[fb_idx] = fb_item
    write_feedback(conv_dir, feedback_items)
    # Persist the lesson-influenced analysis whether validated or not
    # — operator should see what their lesson actually produced.
    write_analysis(conv_dir, new_analysis)
    log.info(
        "Lesson %s.",
        "validated" if final_status == "validated" else "saved as best_effort",
    )
    return {
        "ok": True,
        "lesson_status": final_status,
        "validation_iterations": 1,
        "wrote_analysis": True,
    }


def _valid_lesson_shape(obj: object) -> bool:
    """Gate on LLM-authored lesson JSON: must be a dict with non-empty
    string heading/body/source fields. Any other shape (list, missing
    key, non-string or whitespace-only value) is treated as malformed
    so the caller can return None instead of raising inside
    ``process_feedback``'s unguarded ``lesson_json["heading"]`` indexing.
    """
    if not isinstance(obj, dict):
        return False
    for key in ("heading", "body", "source"):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def _extract_lesson(
    model: str,
    api_key: str,
    conversation_logs: str,
    finding: dict,
    feedback_item: dict,
) -> dict | None:
    """Call the LLM to extract a lesson from operator feedback."""
    finding_text = yaml.dump(finding, default_flow_style=False, sort_keys=False)
    existing_lessons = read_lessons(LESSONS_PATH)
    user_message = (
        f"## Original finding\n\n{finding_text}\n\n"
        f"## Operator feedback\n\n"
        f"Disposition: {feedback_item['disposition']}\n"
        f"Rebuttal: {feedback_item['rebuttal']}\n\n"
    )
    if existing_lessons.strip():
        user_message += f"## Existing lessons\n\n{existing_lessons}\n\n"
    user_message += f"## Conversation logs\n\n{conversation_logs}"

    completion = llm_completion(model, api_key, FEEDBACK_EXTRACT_PROMPT, user_message, max_tokens=1024)
    if completion.text is None:
        _handle_llm_failure(completion, "feedback lesson extraction")
        return None
    parsed = parse_json_response(completion.text)
    if not _valid_lesson_shape(parsed):
        log.warning("Malformed lesson JSON from LLM (got %s)", type(parsed).__name__)
        return None
    return parsed


# ── Main ─────────────────────────────────────────────────────────────────────


def _log_findings(conv_dir: Path, analysis: dict) -> None:
    """Log findings summary for a single analysis."""
    findings = analysis.get("findings", [])
    label = f"{conv_dir.parent.name}/{conv_dir.name}"
    if findings:
        log.info("  %s: %d finding(s)", label, len(findings))
        for f in findings:
            log.info(
                "    [%s] %s — %s",
                f.get("severity", "?"), f.get("failure_mode", "?"), f.get("summary", ""),
            )
    else:
        log.info("  %s: clean", label)


SCAN_LOCK_PATH = _data_dir() / "analysis" / "scan.lock"


@contextlib.contextmanager
def acquire_scan_lock(lock_path: Path = SCAN_LOCK_PATH):
    """Cross-platform non-blocking file lock to serialise scans.

    Yields True when the caller owns the lock, False when another
    scanner already holds it.  A False yield means the caller should
    skip this scan cycle — the other holder will cover the same
    unanalysed conversations.

    Backed by the ``filelock`` library, which uses ``fcntl.flock`` on
    POSIX and ``msvcrt.locking`` on Windows so the same contract holds
    on both platforms.  Cross-OS coordination (Docker-Linux analyser
    loop + Windows-native agent hitting the same mounted data dir) is
    not solvable at the kernel level by any file lock; the lock
    serialises scanners running on the same OS.
    """
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Cannot create scan lock directory %s: %s", lock_path.parent, exc)
        yield False
        return
    lock = FileLock(str(lock_path), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        yield False
        return
    try:
        yield True
    finally:
        lock.release()


def run_scan(
    model: str,
    api_key: str,
    guide_text: str,
    git_ref: str | None,
    staleness_minutes: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Run a single scan. Returns the number of conversations analysed."""
    eligible = find_eligible_conversations(staleness_minutes)
    if not eligible:
        log.info("No eligible conversations found.")
        return 0

    log.info("Found %d eligible conversation(s).", len(eligible))
    analysed = 0

    for i in range(0, len(eligible), batch_size):
        batch = eligible[i : i + batch_size]
        results = analyse_batch(model, api_key, batch, guide_text, git_ref)

        if len(batch) > 1 and not any(results):
            # The whole batch failed — retry each conversation alone so one
            # un-analysable conversation (e.g. too large for the combined call)
            # doesn't take its batch-mates down with it.
            results = [
                analyse_batch(model, api_key, [cd], guide_text, git_ref)[0]
                for cd in batch
            ]

        for conv_dir, analysis in zip(batch, results):
            if analysis:
                write_analysis(conv_dir, analysis)
                analysed += 1
                _log_findings(conv_dir, analysis)

    if analysed:
        collect_metrics()

    return analysed


def run_single_conversation_analysis(
    model: str,
    api_key: str,
    conv_dir: Path,
    guide_text: str,
    git_ref: str | None,
) -> dict:
    """Re-Analyse for a single conversation (the manual ``--conversation``
    path / UI Re-Analyse button).

    Processes pending feedback for ``conv_dir`` only — does not touch
    other conversations' feedback. If feedback processing already
    wrote a fresh ``analysis.yaml`` via the validation loop, the
    explicit ``analyse_batch`` is skipped to avoid a duplicate LLM
    call and a non-deterministic re-roll that could undo the
    validated result. Otherwise (no pending feedback, or feedback
    that produced no write — orphaned, error, all-iterations-failed)
    runs an explicit ``analyse_batch`` and persists.

    Returns ``{"ok": bool, "analysis": dict | None}`` where
    ``analysis`` is the analysis on disk after the run.
    """
    wrote_via_feedback = process_pending_feedback_for(
        conv_dir, model, api_key, guide_text, git_ref,
    )
    consolidate_lessons(model, api_key)

    if wrote_via_feedback:
        analysis_path = conv_dir / ANALYSIS_FILENAME
        if analysis_path.exists():
            analysis = yaml.safe_load(analysis_path.read_text(encoding="utf-8"))
            collect_metrics()
            return {"ok": True, "analysis": analysis}
        return {"ok": False, "analysis": None}

    results = analyse_batch(model, api_key, [conv_dir], guide_text, git_ref)
    analysis = results[0] if results else None
    if analysis is None:
        return {"ok": False, "analysis": None}
    write_analysis(conv_dir, analysis)
    collect_metrics()
    return {"ok": True, "analysis": analysis}


def prepare_context(
    model_override: str | None = None,
) -> tuple[str, str, str, str | None]:
    """Resolve the four inputs every analyser entry function needs.

    Returns ``(model, api_key, guide_text, git_ref)``. Raises
    ``AnalyserFatalError`` if the guide is missing, no model is
    configured, or no API key is available for the resolved provider.

    Used by every entry into the analyser:

    - The CLI's ``main()`` (with the optional ``--model`` override).
    - ``analysis_browser.run_analysis`` / ``scan_and_analyse`` for the
      web UI buttons.
    - The background loop in ``analyser.loop``.

    Centralising the resolution here means all three paths agree on
    model precedence, guide location, and fatal-error semantics.
    """
    if not GUIDE_PATH.exists():
        raise AnalyserFatalError(f"Analyser guide not found at {GUIDE_PATH}")
    guide_text = GUIDE_PATH.read_text(encoding="utf-8")

    model = resolve_model(model_override)
    if not model:
        raise AnalyserFatalError(
            "No analyser model configured. "
            "Set LLM_MODEL_ANALYSER or LLM_MODEL in your .env, "
            "or pass --model provider/model on the CLI.",
        )
    api_key = resolve_api_key(model)
    git_ref = get_git_ref()
    return model, api_key, guide_text, git_ref


def main() -> None:
    if litellm is None:
        print("Error: litellm package not installed. Run: pip install litellm")
        sys.exit(1)
    if yaml is None:
        print("Error: pyyaml package not installed. Run: pip install pyyaml")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Analyse Swarpius conversations using LiteLLM",
    )
    parser.add_argument(
        "--conversation", "-c",
        help="Analyse a specific conversation (e.g., 'c01' or '2026-03-28/c01')",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously, scanning at regular intervals",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_MINUTES,
        help=f"Scan interval in minutes (default: {DEFAULT_INTERVAL_MINUTES})",
    )
    parser.add_argument(
        "--staleness", type=int, default=DEFAULT_STALENESS_MINUTES,
        help=f"Staleness threshold in minutes (default: {DEFAULT_STALENESS_MINUTES})",
    )
    parser.add_argument(
        "--model", default=None,
        help="LLM model in provider/model format. Defaults to LLM_MODEL_ANALYSER, then LLM_MODEL from agent/.env.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=resolve_batch_size(),
        help=(
            "Conversations per API call (default: ANALYSER_BATCH_SIZE env, "
            f"or {DEFAULT_BATCH_SIZE})"
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    model, api_key, guide_text, git_ref = prepare_context(args.model)
    log.info("Model: %s | Git ref: %s", model, git_ref or "unknown")

    if args.conversation:
        conv_dir = resolve_conversation_path(args.conversation)
        if conv_dir is None:
            log.error("Conversation not found: %s", args.conversation)
            sys.exit(1)

        # Serialise against the scheduled scanner and any concurrent
        # single-conv invocations (e.g. two Re-Analyse clicks in quick
        # succession) — without this lock, two subprocesses could race
        # on feedback.yaml reads/writes and produce duplicate history
        # entries. EX_TEMPFAIL tells callers to retry later.
        with acquire_scan_lock() as acquired:
            if not acquired:
                log.info("Another scan is in progress — exiting.")
                sys.exit(75)

            result = run_single_conversation_analysis(
                model, api_key, conv_dir, guide_text, git_ref,
            )
            analysis = result.get("analysis")
            if result.get("ok") and analysis is not None:
                findings = analysis.get("findings", [])
                print(f"\nAnalysis complete: {len(findings)} finding(s)")
                for f in findings:
                    print(f"  [{f.get('severity', '?')}] {f.get('failure_mode', '?')} — {f.get('summary', '')}")
                if not findings:
                    print("  No issues found.")
            else:
                print("Analysis failed — check logs.")
                sys.exit(1)
        return

    log.info("Staleness threshold: %d min | Scope: today + yesterday", args.staleness)

    if args.loop:
        log.info("Loop mode: scanning every %d minutes. Ctrl+C to stop.", args.interval)
        try:
            while True:
                with acquire_scan_lock() as acquired:
                    if acquired:
                        process_all_pending_feedback(model, api_key, guide_text, git_ref)
                        consolidate_lessons(model, api_key)
                        run_scan(model, api_key, guide_text, git_ref, args.staleness, args.batch_size)
                    else:
                        log.info("Another scan is in progress — skipping this tick.")
                log.info("Next scan in %d minutes...", args.interval)
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            log.info("Stopped.")
    else:
        with acquire_scan_lock() as acquired:
            if not acquired:
                log.info("Another scan is in progress — exiting.")
                sys.exit(75)  # EX_TEMPFAIL: transient failure, retry later
            process_all_pending_feedback(model, api_key, guide_text, git_ref)
            consolidate_lessons(model, api_key)
            count = run_scan(model, api_key, guide_text, git_ref, args.staleness, args.batch_size)
            log.info("Done. Analysed %d conversation(s).", count)


if __name__ == "__main__":
    try:
        main()
    except AnalyserFatalError as exc:
        log.error("%s", exc)
        sys.exit(1)
