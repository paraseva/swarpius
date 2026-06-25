from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.data_paths import conversation_logs_dir, feedback_archive_dir
from app.runtime.conversation_tracker import ConversationTracker, day_str
from app.settings import get_settings
from app.time_utils import local_now, local_today

_log = logging.getLogger("swarpius.request_logger")


class _LiteralStr(str):
    """Marker for strings that should use YAML literal block scalar (|)."""


def _literal_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


_MAX_LINE_WIDTH = 100


def _wrap_long_lines(text: str) -> str:
    """Soft-wrap lines longer than _MAX_LINE_WIDTH for readability.

    Preserves lines that are part of JSON structure (object/array
    nesting, quoted keys). All other long lines — prose, XML tags,
    markdown bullets — are wrapped.
    """
    import textwrap

    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        is_json_structure = stripped[:1] in ("{", "}", "]") or (
            stripped.startswith('"') and '": ' in stripped
        )
        if len(line) <= _MAX_LINE_WIDTH or is_json_structure:
            out.append(line)
        else:
            indent = len(line) - len(stripped)
            wrapped = textwrap.fill(
                line,
                width=_MAX_LINE_WIDTH,
                initial_indent=" " * indent,
                subsequent_indent=" " * (indent + 2),
            )
            out.append(wrapped)
    return "\n".join(out)


def _prepare_for_yaml(data: Any) -> Any:
    """Recursively mark multiline strings so YAML renders them with | blocks."""
    if isinstance(data, str):
        if "\n" in data:
            return _LiteralStr(_wrap_long_lines(data))
        if len(data) > _MAX_LINE_WIDTH:
            return _LiteralStr(_wrap_long_lines(data))
        return data
    if isinstance(data, dict):
        return {k: _prepare_for_yaml(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_prepare_for_yaml(item) for item in data]
    return data

_CONVERSATION_DIR_RE = re.compile(r"^c(\d+)$")
_REQUEST_DIR_RE = re.compile(r"^rq-c\d+-(\d+)$")


def extract_conversation_dir(request_id: str) -> str:
    """Extract conversation directory name from request ID.

    ``rq-c01-0003`` → ``c01``.  Falls back to ``c00`` for legacy or
    unrecognised formats.
    """
    parts = request_id.split("-")
    if len(parts) >= 3 and parts[1].startswith("c"):
        return parts[1]
    return "c00"


class RequestIdGenerator:
    """Generates sequential request IDs grouped by conversation.

    Format: ``rq-cNN-NNNN`` where *NN* is the conversation counter and *NNNN*
    is a request sequence that increments monotonically within the session.
    Conversation assignment is delegated to a :class:`ConversationTracker`.

    On construction the generator scans today's log directory to resume
    counters from where the previous session left off, so a restart does
    not overwrite existing logs. When state persistence is wired the full
    tracker state is restored instead (the log scan is the fresh-DB fallback).

    Process-level (one per agent process, on ``RuntimeState``) so conversation
    grouping is independent of the transport and survives reconnects; a lock
    guards the mint + persistence snapshot against cross-thread access.
    """

    # Persistence participant key (structurally satisfies PersistentState).
    state_key = "conversation_tracker"

    def __init__(
        self,
        idle_timeout_seconds: Optional[int] = None,
        logs_root: Optional[Path] = None,
        tracker: Optional[ConversationTracker] = None,
    ) -> None:
        if idle_timeout_seconds is None:
            idle_timeout_seconds = get_settings().conversation_idle_timeout_seconds
        conversation_num, conv_sequences = self._resume_counters(logs_root)
        if tracker is not None:
            self._tracker = tracker
        else:
            self._tracker = ConversationTracker(
                idle_timeout_seconds=idle_timeout_seconds,
                start_conversation_num=conversation_num,
            )
        self._sequence: int = 0
        self._conv_sequences: dict[str, int] = conv_sequences
        self._last_conv_id: str = self._tracker.current_id
        self._day: str = day_str(self._tracker.now)
        self._lock = threading.Lock()

    @property
    def tracker(self) -> ConversationTracker:
        """Access the underlying ConversationTracker."""
        return self._tracker

    @staticmethod
    def _resume_counters(logs_root: Optional[Path] = None) -> tuple[int, dict[str, int]]:
        """Scan today's log directory and return (next_conversation, per_conv_sequences).

        If no logs exist for today, returns (1, {}) — the default fresh state.
        Otherwise returns (max_conversation + 1, {conv_id: max_seq, ...}) so
        that any conversation revisited after a restart or reconnect continues
        its sequence rather than overwriting from 0001.
        """
        root = logs_root or conversation_logs_dir()
        today_dir = root / local_today()
        if not today_dir.is_dir():
            return 1, {}

        max_conversation = 0
        conv_sequences: dict[str, int] = {}

        for conv_entry in today_dir.iterdir():
            if not conv_entry.is_dir():
                continue
            conv_match = _CONVERSATION_DIR_RE.match(conv_entry.name)
            if not conv_match:
                continue
            conv_num = int(conv_match.group(1))
            conv_id = conv_entry.name  # e.g. "c03"
            if conv_num > max_conversation:
                max_conversation = conv_num

            max_seq = 0
            for req_entry in conv_entry.iterdir():
                if not req_entry.is_dir():
                    continue
                req_match = _REQUEST_DIR_RE.match(req_entry.name)
                if req_match:
                    seq_num = int(req_match.group(1))
                    if seq_num > max_seq:
                        max_seq = seq_num
            if max_seq > 0:
                conv_sequences[conv_id] = max_seq

        if max_conversation == 0:
            return 1, {}

        return max_conversation + 1, conv_sequences

    def next_id(self) -> str:
        # `_sequence` is the in-flight counter for `_last_conv_id`.
        # `_conv_sequences` is the save-state map (last-used sequence per
        # conversation). On a conversation switch — whether driven by idle
        # timeout or by the diagnostic agent's reassign_current() — we save
        # the outgoing conversation's counter BEFORE loading the incoming
        # one. The save always writes the highest sequence we produced for
        # `_last_conv_id` (in-memory authority is the freshest), and the
        # load falls back to the on-disk-resumed value (or 0 for a fresh
        # conversation) only after the save is committed. No stale overwrite
        # path exists in either direction.
        with self._lock:
            self._roll_day()
            conv_id = self._tracker.assign_by_timeout()
            if conv_id != self._last_conv_id:
                self._conv_sequences[self._last_conv_id] = self._sequence
                self._sequence = self._conv_sequences.get(conv_id, 0)
            self._last_conv_id = conv_id
            self._sequence += 1
            return f"rq-{conv_id}-{self._sequence:04d}"

    def roll_day(self) -> None:
        """Start a fresh conversation grouping if the calendar day has changed.

        Called before conversation classification so the diagnostic agent sees a
        clean slate on a new day and cannot continue a previous day's thread.
        ``next_id`` rolls too, so the no-classifier path is covered as well."""
        with self._lock:
            self._roll_day()

    def _roll_day(self) -> None:
        """Reset conversation grouping + the request sequence when the day has
        changed since the last request. Caller holds ``_lock``. The day is the
        wall-clock day of the tracker clock, matching the log-directory date."""
        today = day_str(self._tracker.now)
        if today != self._day:
            self._tracker.reset()
            self._sequence = 0
            self._conv_sequences = {}
            self._last_conv_id = self._tracker.current_id
            self._day = today

    def capture_state(self) -> Dict[str, Any]:
        """Snapshot the ID counters + the tracker, so conversation grouping
        and numbering continue after a restart."""
        with self._lock:
            return {
                "sequence": self._sequence,
                "conv_sequences": dict(self._conv_sequences),
                "last_conv_id": self._last_conv_id,
                "tracker": self._tracker.capture_state(),
            }

    def restore_state(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._sequence = data.get("sequence", 0)
            self._conv_sequences = dict(data.get("conv_sequences", {}))
            self._last_conv_id = data.get("last_conv_id") or self._tracker.current_id
            tracker_data = data.get("tracker")
            if tracker_data:
                self._tracker.restore_state(tracker_data)
            # Seed the day from the restored state so the next request rolls to a
            # fresh grouping if it lands on a later calendar day, and continues
            # otherwise. Derived from the tracker (no separate persisted field).
            self._day = self._tracker.current_day or day_str(self._tracker.now)

    def new_conversation(self) -> None:
        """Explicitly bump the conversation counter (e.g. on reconnect)."""
        self._tracker.new_conversation()

    @property
    def conversation_id(self) -> str:
        return self._tracker.current_id


def _safe_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


class RequestLogger:
    def __init__(self, request_id: str, logs_root: Optional[Path] = None) -> None:
        self.request_id = request_id
        self._logs_root = logs_root or conversation_logs_dir()
        date_str = local_today()
        conversation_dir = extract_conversation_dir(request_id)
        self._request_dir = self._logs_root / date_str / conversation_dir / request_id
        self._request_dir.mkdir(parents=True, exist_ok=True)
        (self._request_dir / "coordinator_steps").mkdir(exist_ok=True)
        (self._request_dir / "tool_executions").mkdir(exist_ok=True)
        (self._request_dir / "prompts").mkdir(exist_ok=True)
        self._events_path = self._request_dir / "events.jsonl"
        self._tool_execution_counter = 0
        self._start_time = time.perf_counter()
        self._start_timestamp = local_now().isoformat()
        self._write_warned = False

    def _write_json(self, path: Path, data: Any) -> None:
        try:
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        except Exception:
            if not self._write_warned:
                _log.warning("Failed to write request log: %s", path, exc_info=True)
                self._write_warned = True

    @staticmethod
    def _write_yaml(path: Path, data: Any) -> None:
        try:
            dumper = yaml.Dumper
            dumper.add_representer(_LiteralStr, _literal_representer)
            path.write_text(
                yaml.dump(
                    _prepare_for_yaml(data),
                    Dumper=dumper,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    width=120,
                ),
                encoding="utf-8",
            )
        except Exception:
            _log.warning("Failed to write request log: %s", path, exc_info=True)

    def append_event(self, channel: str, payload: Any) -> None:
        try:
            entry = {
                "timestamp_ms": int(time.time() * 1000),
                "elapsed_ms": int((time.perf_counter() - self._start_time) * 1000),
                "channel": channel,
                "payload": _safe_json(payload),
            }
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            if not self._write_warned:
                _log.warning("Failed to append event log: %s", self._events_path, exc_info=True)
                self._write_warned = True

    def handle(self, event: Any) -> None:
        """Bus subscriber: write each AgentEvent to events.jsonl as a
        chronological record of the request lifecycle. Works in every
        transport — events.jsonl is now driven directly by the bus
        rather than as a side effect of WS emission."""
        from dataclasses import asdict, is_dataclass
        if is_dataclass(event):
            payload = asdict(event)
            event_type = type(event).__name__
        else:
            payload = {"value": str(event)}
            event_type = type(event).__name__
        try:
            entry = {
                "timestamp_ms": int(time.time() * 1000),
                "elapsed_ms": int((time.perf_counter() - self._start_time) * 1000),
                "event_type": event_type,
                "payload": _safe_json(payload),
            }
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            if not self._write_warned:
                _log.warning("Failed to append event log: %s", self._events_path, exc_info=True)
                self._write_warned = True

    def log_request(self, user_input: str, run_mode: str) -> None:
        self._write_json(
            self._request_dir / "request.json",
            {
                "request_id": self.request_id,
                "user_input": user_input,
                "run_mode": run_mode,
                "timestamp": self._start_timestamp,
                "timestamp_ms": int(time.time() * 1000),
            },
        )

    def log_coordinator_step(
        self,
        step: int,
        coordinator_input: Any,
        coordinator_output: Any,
        context_snapshot: Optional[Dict[str, str]] = None,
        duration_ms: Optional[int] = None,
        usage: Optional[Dict[str, int]] = None,
    ) -> None:
        self._write_yaml(
            self._request_dir / "coordinator_steps" / f"step_{step:02d}.yaml",
            {
                "request_id": self.request_id,
                "step": step,
                "coordinator_input": _safe_json(coordinator_input),
                "coordinator_output": _safe_json(coordinator_output),
                "context_providers": context_snapshot,
                "duration_ms": duration_ms,
                "usage": usage,
            },
        )

    def log_tool_execution(
        self,
        step: int,
        selected_skill: str,
        tool_input: Any,
        tool_output: Any,
        duration_ms: int,
        attempt: int = 1,
        retry_notes: Optional[List[str]] = None,
        error: Optional[str] = None,
    ) -> None:
        self._tool_execution_counter += 1
        self._write_json(
            self._request_dir / "tool_executions" / f"{self._tool_execution_counter:02d}_{selected_skill}.json",
            {
                "request_id": self.request_id,
                "step": step,
                "selected_skill": selected_skill,
                "tool_input": _safe_json(tool_input),
                "tool_output": _safe_json(tool_output),
                "duration_ms": duration_ms,
                "attempt": attempt,
                "retry_notes": retry_notes,
                "error": error,
            },
        )

    def log_prompt_snapshot(
        self,
        agent_name: str,
        system_prompt: Optional[str] = None,
        context_providers: Optional[Dict[str, str]] = None,
        step: Optional[int] = None,
    ) -> None:
        if system_prompt:
            filename = f"{agent_name.lower().replace(' ', '_')}_system.txt"
            try:
                (self._request_dir / "prompts" / filename).write_text(
                    system_prompt, encoding="utf-8",
                )
            except Exception:
                # Prompt-dump is diagnostic-only; a disk failure here
                # must not take down the request itself.
                pass
        if context_providers:
            suffix = f"_step_{step:02d}" if step is not None else ""
            self._write_yaml(
                self._request_dir / "prompts" / f"context_providers{suffix}.yaml",
                context_providers,
            )

    def log_outcome(
        self,
        status: str,
        chat_response: Optional[str] = None,
        problem_description: Optional[str] = None,
        total_steps: int = 0,
        total_duration_ms: Optional[int] = None,
        flags: Optional[List[str]] = None,
        topic_summary: Optional[str] = None,
        assignment_source: Optional[str] = None,
        coordinator_model: Optional[str] = None,
        usage: Optional[Dict[str, int]] = None,
    ) -> None:
        outcome_data: Dict[str, Any] = {
            "request_id": self.request_id,
            "status": status,
            "chat_response": chat_response,
            "problem_description": problem_description,
            "total_steps": total_steps,
            "total_duration_ms": total_duration_ms or int((time.perf_counter() - self._start_time) * 1000),
            "timestamp": local_now().isoformat(),
        }
        if coordinator_model is not None:
            outcome_data["coordinator_model"] = coordinator_model
        if topic_summary is not None:
            outcome_data["topic_summary"] = topic_summary
        if assignment_source is not None:
            outcome_data["assignment_source"] = assignment_source
        if usage:
            outcome_data["usage"] = usage
        self._write_json(self._request_dir / "outcome.json", outcome_data)

        if flags:
            self._write_json(
                self._request_dir / "flagged.json",
                {
                    "request_id": self.request_id,
                    "flags": flags,
                    "timestamp": local_now().isoformat(),
                },
            )

    def write_context_snapshot(self, data: Dict[str, Any]) -> bool:
        """Write context_snapshot.json at the conversation directory level.

        First-request-wins: if the snapshot already exists for this
        conversation, leave it alone and return False.  Subsequent
        requests in the same conversation reuse the original snapshot
        so the analyser reads state as it was when the conversation
        began, not as it is at analysis time.

        Returns True when a new file is written, False when one
        already existed.
        """
        conversation_dir = self._request_dir.parent
        snapshot_path = conversation_dir / "context_snapshot.json"
        if snapshot_path.exists():
            return False
        payload = {"timestamp": local_now().isoformat(), **data}
        self._write_json(snapshot_path, payload)
        return True

    def update_conversation_summary(self, topic_summary: Optional[str] = None) -> None:
        """Write/update conversation_summary.json in the conversation (cXX) directory.

        Always appends the request_id to the requests list.
        ``topic_summary=None`` preserves any existing topic.
        """
        conversation_dir = self._request_dir.parent
        summary_path = conversation_dir / "conversation_summary.json"
        conversation_id = extract_conversation_dir(self.request_id)

        existing: Dict[str, Any] = {}
        if summary_path.exists():
            try:
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable summary — start fresh rather
                # than block this request on a stale log file.
                pass

        requests: List[str] = existing.get("requests", [])
        if self.request_id not in requests:
            requests.append(self.request_id)

        effective_topic = topic_summary if topic_summary is not None else existing.get("topic_summary")

        self._write_json(summary_path, {
            "conversation_id": conversation_id,
            "topic_summary": effective_topic,
            "requests": requests,
            "updated_at": local_now().isoformat(),
        })

    @property
    def request_dir(self) -> Path:
        return self._request_dir


class NullRequestLogger:
    """A do-nothing logger that satisfies the RequestLogger interface without writing to disk."""

    def __init__(self, request_id: str = "rq-c00-0000") -> None:
        self.request_id = request_id

    def append_event(self, channel: str, payload: Any) -> None:
        pass

    def handle(self, event: Any) -> None:
        pass

    def log_request(self, user_input: str, run_mode: str) -> None:
        pass

    def log_coordinator_step(self, **kwargs: Any) -> None:
        pass

    def log_tool_execution(self, **kwargs: Any) -> None:
        pass

    def log_prompt_snapshot(self, **kwargs: Any) -> None:
        pass

    def log_outcome(self, **kwargs: Any) -> None:
        pass

    def update_conversation_summary(self, **kwargs: Any) -> None:
        pass

    def write_context_snapshot(self, data: Dict[str, Any]) -> bool:
        return False


def _archive_feedback(date_dir: Path, archive_root: Path) -> None:
    """Copy feedback.yaml (and analysis.yaml for context) to the archive."""
    for conv_dir in sorted(date_dir.iterdir()):
        if not conv_dir.is_dir():
            continue
        feedback_file = conv_dir / "feedback.yaml"
        if not feedback_file.exists():
            continue
        dest = archive_root / date_dir.name / conv_dir.name
        if dest.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(feedback_file, dest / "feedback.yaml")
        analysis_file = conv_dir / "analysis.yaml"
        if analysis_file.exists():
            shutil.copy2(analysis_file, dest / "analysis.yaml")


def cleanup_old_logs(logs_root: Optional[Path] = None, retention_days: int = 7) -> int:
    root = logs_root or conversation_logs_dir()
    if not root.is_dir():
        return 0
    cutoff = (local_now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    archive_root = feedback_archive_dir()
    removed = 0
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name < cutoff:
            try:
                _archive_feedback(entry, archive_root)
                shutil.rmtree(entry)
                removed += 1
            except Exception:
                # Retention cleanup runs across many dated dirs at
                # startup — one bad directory (locked file, permission,
                # corrupt archive) must not abort the others. Log and
                # carry on so the operator can investigate offline.
                _log.warning("retention cleanup failed for %s", entry, exc_info=True)
    return removed


def get_retention_days() -> int:
    from app.settings import get_settings
    return get_settings().log_retention_days
