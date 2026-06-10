"""Feedback storage, lessons management, and validation checks for the analyser.

This module provides the non-API-calling logic for the feedback system:
- Reading/writing feedback.yaml files alongside analysis.yaml
- Managing lessons-learned.md (read, write, update individual lessons)
- Building analyser prompts with lessons injected
- Deterministic validation checks comparing original vs re-analysis findings
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

_log = logging.getLogger("analyse.feedback")

FEEDBACK_FILENAME = "feedback.yaml"
VALID_DISPOSITIONS = ("dismiss", "downgrade")
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

LESSONS_HEADER = """\
# Lessons Learned

Contextual knowledge accumulated from operator feedback on analysis findings.
Apply these lessons when evaluating conversations — they represent domain
knowledge not covered in the analysis guide.
"""


# ---------------------------------------------------------------------------
# Feedback storage
# ---------------------------------------------------------------------------


def read_feedback(conv_dir: Path) -> list[dict]:
    """Read feedback items from feedback.yaml in a conversation directory.

    Returns an empty list if the file doesn't exist or is empty/unparseable.
    """
    path = conv_dir / FEEDBACK_FILENAME
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        _log.warning("Failed to read feedback file: %s", path, exc_info=True)
        return []


def write_feedback(conv_dir: Path, feedback: list[dict]) -> None:
    """Write feedback items to feedback.yaml in a conversation directory."""
    path = conv_dir / FEEDBACK_FILENAME
    path.write_text(
        yaml.dump(feedback, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def add_feedback_item(
    conv_dir: Path,
    request_id: str,
    failure_mode: str,
    disposition: str,
    rebuttal: str,
) -> dict:
    """Add or replace a feedback item in feedback.yaml. Returns the item.

    Findings are identified by ``(request_id, failure_mode)`` — the same
    identity used by the websocket-facing submit_feedback and by
    process_feedback's cross-analysis lookup. If an entry for the same
    identity already exists, it is replaced in place; otherwise a new
    entry is appended. This matches the operator refinement flow (submit
    dismiss, change mind, submit downgrade → one entry, latest wins).

    Raises ValueError for empty identity fields, invalid disposition, or
    empty rebuttal.
    """
    if not request_id.strip():
        raise ValueError("request_id must not be empty")
    if not failure_mode.strip():
        raise ValueError("failure_mode must not be empty")
    if disposition not in VALID_DISPOSITIONS:
        raise ValueError(
            f"Invalid disposition {disposition!r}. Must be one of: {', '.join(VALID_DISPOSITIONS)}"
        )
    if not rebuttal.strip():
        raise ValueError("rebuttal must not be empty")

    item = {
        "request_id": request_id,
        "failure_mode": failure_mode,
        "disposition": disposition,
        "rebuttal": rebuttal.strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lesson_status": "pending",
        "validation_iterations": 0,
    }

    existing = read_feedback(conv_dir)
    for i, entry in enumerate(existing):
        if (entry.get("request_id") == request_id
                and entry.get("failure_mode") == failure_mode):
            existing[i] = item
            break
    else:
        existing.append(item)
    write_feedback(conv_dir, existing)
    return item


# ---------------------------------------------------------------------------
# Lessons management
# ---------------------------------------------------------------------------


def read_lessons(lessons_path: Path) -> str:
    """Read lessons-learned.md content. Returns empty string if missing."""
    if not lessons_path.exists():
        return ""
    try:
        return lessons_path.read_text(encoding="utf-8")
    except Exception:
        _log.warning("Failed to read lessons file: %s", lessons_path, exc_info=True)
        return ""


def count_lessons(lessons_path: Path) -> int:
    """Return the number of lessons in lessons-learned.md."""
    content = read_lessons(lessons_path)
    if not content.strip():
        return 0
    return len(_parse_lessons(content))


def _parse_lessons(content: str) -> list[dict]:
    """Parse lessons-learned.md into a list of {heading, body, source} dicts.

    Each lesson starts with a ## heading. The body is everything up to the
    next ## heading or end of file. The *Source:* line (if present) is
    extracted separately.
    """
    lessons: list[dict] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                _flush_lesson(lessons, current_heading, current_lines)
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    if current_heading is not None:
        _flush_lesson(lessons, current_heading, current_lines)

    return lessons


def _flush_lesson(lessons: list[dict], heading: str, lines: list[str]) -> None:
    """Extract body and source from accumulated lines, append to lessons list."""
    source = ""
    body_lines = []
    for line in lines:
        if line.startswith("*Source:"):
            source = line.strip("* \n")
            if source.startswith("Source:"):
                source = source[len("Source:"):].strip()
        else:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    lessons.append({"heading": heading, "body": body, "source": source})


def write_lesson(
    lessons_path: Path,
    heading: str,
    body: str,
    source: str,
) -> str:
    """Add or update a lesson in lessons-learned.md. Returns updated content.

    Dedup key is ``(heading, source)``: two feedback items that happen to
    produce the same heading but come from different source requests each
    get their own entry — neither loses its attribution. A write with the
    same (heading, source) pair replaces the existing entry, so retry after
    a partial failure is idempotent (the lesson doesn't duplicate).

    Creates the file with a header if it doesn't exist.
    """
    content = read_lessons(lessons_path)
    lessons = _parse_lessons(content) if content else []

    new_lesson = {"heading": heading, "body": body, "source": source}

    # Update existing or append — match on (heading, source).
    updated = False
    for i, lesson in enumerate(lessons):
        if lesson["heading"] == heading and lesson["source"] == source:
            lessons[i] = new_lesson
            updated = True
            break
    if not updated:
        lessons.append(new_lesson)

    parts = [LESSONS_HEADER.rstrip()]
    for lesson in lessons:
        parts.append(f"\n## {lesson['heading']}\n")
        parts.append(lesson["body"])
        if lesson["source"]:
            parts.append(f"\n*Source: {lesson['source']}*")

    result = "\n".join(parts) + "\n"
    lessons_path.write_text(result, encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def build_analyser_prompt(guide_text: str, lessons_path: Path) -> str:
    """Build the full analyser system prompt: guide + lessons (if any)."""
    lessons = read_lessons(lessons_path)
    if not lessons.strip():
        return guide_text
    return f"{guide_text}\n\n{lessons}"


# ---------------------------------------------------------------------------
# Validation check
# ---------------------------------------------------------------------------


def check_finding_resolved(
    original_finding: dict,
    new_analysis: dict,
    disposition: str,
) -> str:
    """Check whether a disputed finding was resolved in the re-analysis.

    Compares the original finding against the new analysis output.
    Matching is by (request_id, failure_mode).

    Returns:
        "validated"    — the finding changed as the disposition required
        "not_resolved" — the finding did not change appropriately
    """
    findings = new_analysis.get("findings", [])
    request_id = original_finding["request_id"]
    failure_mode = original_finding["failure_mode"]

    match = None
    for f in findings:
        if f.get("request_id") == request_id and f.get("failure_mode") == failure_mode:
            match = f
            break

    if disposition == "dismiss":
        return "validated" if match is None else "not_resolved"

    if disposition == "downgrade":
        if match is None:
            # Gone entirely — better than expected
            return "validated"
        orig_sev = SEVERITY_ORDER.get(original_finding.get("severity", ""), 0)
        new_sev = SEVERITY_ORDER.get(match.get("severity", ""), 0)
        return "validated" if new_sev < orig_sev else "not_resolved"

    return "not_resolved"
