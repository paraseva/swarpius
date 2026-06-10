"""Operator-side feedback storage for analysed conversations.

Stores per-finding disputes alongside the conversation log. The passive
analyser (``passive-analyser/feedback.py``) reads these entries, generates
lessons from them, and clears or updates them as it processes each one.

Findings are identified by ``(request_id, failure_mode)`` so the identity
survives re-analysis even when the finding list is regenerated.

Per-conversation lock: only one dispute can be active at a time on a given
conversation. Each dispute's lesson reshapes the whole analysis on re-run, so
concurrent disputes on different findings aren't coherent — the second would
often target a finding that no longer exists after the first lesson applies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.coordinator.parse_helpers import safe_parse_yaml, safe_parse_yaml_list

FEEDBACK_FILENAME = "feedback.yaml"
VALID_DISPOSITIONS = ("dismiss", "downgrade")
_ACTIVE_STATUSES = ("pending", "processing")


def _find_entry(items: list[dict], request_id: str, failure_mode: str) -> int | None:
    for i, entry in enumerate(items):
        if (entry.get("request_id") == request_id
                and entry.get("failure_mode") == failure_mode):
            return i
    return None


def submit_feedback(
    logs_root: Path,
    date: str,
    conversation_id: str,
    request_id: str,
    failure_mode: str,
    disposition: str,
    rebuttal: str,
) -> dict[str, Any]:
    """Store operator feedback for a specific finding.

    Same-identity re-submits are allowed when the existing entry is pending
    (refinement — operator changed their mind) but rejected when processing
    (analyser is currently working on it).
    """
    if disposition not in VALID_DISPOSITIONS:
        return {"ok": False, "error": f"Invalid disposition: {disposition}"}
    if not rebuttal.strip():
        return {"ok": False, "error": "Rebuttal must not be empty"}

    conv_dir = logs_root / date / conversation_id
    if not conv_dir.is_dir():
        return {"ok": False, "error": f"Conversation not found: {date}/{conversation_id}"}

    analysis = safe_parse_yaml(conv_dir / "analysis.yaml")
    if analysis is None:
        return {"ok": False, "error": "No analysis.yaml found"}

    findings = analysis.get("findings", [])
    if not any(
        f.get("request_id") == request_id and f.get("failure_mode") == failure_mode
        for f in findings
    ):
        return {
            "ok": False,
            "error": (
                f"No finding matches identity {request_id}/{failure_mode} in "
                f"current analysis — the analysis may have been superseded; "
                f"please refresh."
            ),
        }

    fb_path = conv_dir / FEEDBACK_FILENAME
    existing: list[dict] = safe_parse_yaml_list(fb_path)

    # Reject if any other-identity entry is active, or if the same-identity
    # entry is already being processed.
    for entry in existing:
        status = entry.get("lesson_status")
        if status not in _ACTIVE_STATUSES:
            continue
        same_identity = (
            entry.get("request_id") == request_id
            and entry.get("failure_mode") == failure_mode
        )
        if not same_identity:
            scope = "in progress" if status == "processing" else "pending"
            return {
                "ok": False,
                "error": (
                    f"Another dispute is {scope} on this conversation — wait "
                    "for it to be processed, then dispute again if the "
                    "finding is still present."
                ),
            }
        if status == "processing":
            return {
                "ok": False,
                "error": (
                    "Re-analysis is in progress for this dispute — it can't "
                    "be changed until processing completes."
                ),
            }

    item = {
        "request_id": request_id,
        "failure_mode": failure_mode,
        "disposition": disposition,
        "rebuttal": rebuttal.strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lesson_status": "pending",
        "validation_iterations": 0,
    }

    idx = _find_entry(existing, request_id, failure_mode)
    if idx is not None:
        existing[idx] = item
    else:
        existing.append(item)

    fb_path.write_text(
        yaml.dump(existing, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {"ok": True, "item": item}


def get_feedback_status(
    logs_root: Path,
    date: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Return feedback items for a conversation, if any."""
    conv_dir = logs_root / date / conversation_id
    return {
        "ok": True,
        "items": safe_parse_yaml_list(conv_dir / FEEDBACK_FILENAME),
    }


def delete_feedback(
    logs_root: Path,
    date: str,
    conversation_id: str,
    request_id: str,
    failure_mode: str,
) -> dict[str, Any]:
    """Delete a feedback entry by identity. Used by the Cancel button.

    Only pending / resolved entries can be deleted. Processing entries are
    locked — the analyser is actively working on them and deleting would
    corrupt the in-flight run.
    """
    conv_dir = logs_root / date / conversation_id
    if not conv_dir.is_dir():
        return {"ok": False, "error": f"Conversation not found: {date}/{conversation_id}"}

    fb_path = conv_dir / FEEDBACK_FILENAME
    existing: list[dict] = safe_parse_yaml_list(fb_path)
    idx = _find_entry(existing, request_id, failure_mode)
    if idx is None:
        return {
            "ok": False,
            "error": f"No feedback entry for identity {request_id}/{failure_mode}",
        }

    if existing[idx].get("lesson_status") == "processing":
        return {
            "ok": False,
            "error": (
                "Re-analysis is in progress for this dispute — it can't be "
                "cancelled until processing completes."
            ),
        }

    existing.pop(idx)
    if existing:
        fb_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    elif fb_path.exists():
        fb_path.unlink()
    return {"ok": True}
