"""Diagnostic agent for LLM-driven conversation classification.

A lightweight agent that semantically classifies each user request into
a conversation thread. Runs before the coordinator tool-calling loop and
on the critical path: ``process_request`` awaits the classification (5s
timeout) to mint the request ID, then runs the coordinator. A haiku-tier
model keeps the added latency small.

Designed as a multi-function dispatcher; conversation assignment is the
first function. Future functions: complexity assessment, anomaly detection,
session summarisation, feedback classification.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from app.io.cost_ledger import record_cost_from_usage
from app.llm.client import LLMClient
from app.llm.json_extract import extract_json_object
from app.runtime.conversation_tracker import ConversationThread, ConversationTracker

_log = logging.getLogger("swarpius.diagnostic_agent")

def is_diagnostic_agent_enabled() -> bool:
    """Whether the diagnostic agent is enabled. Resolved through the
    locked-at-startup settings cache (see ``app.settings``)."""
    from app.settings import get_settings
    return get_settings().enable_diagnostic_agent

SYSTEM_PROMPT = """\
You classify user requests into conversation threads for a music assistant.

Given a user message and a list of active conversation threads, decide:
1. Does this message belong to an existing conversation? → Return that conversation's ID.
2. Is this a new topic? → Return is_new: true with a brief topic summary.

Respond with JSON only:
{"conversation_id": "c01", "is_new": false, "topic_summary": "Updated summary"}
or
{"is_new": true, "topic_summary": "New topic description"}

Rules:
- Topic summaries should be concise (under 60 characters).
- Only create a new conversation if the topic is genuinely different.
- Generic or acknowledgement messages (thanks, ok, great, sure, yes, no, etc.) always belong to the most recently active conversation.
- Follow-on music requests (playing different tracks, browsing related items, asking about the same or a related artist) within the same listening session are one conversation.
- When in doubt, prefer the most recently active conversation.
- Requests about system configuration or debugging are separate from music requests."""


_MAX_SUMMARY_LEN = 120


def truncate_response(text: str, max_len: int = _MAX_SUMMARY_LEN) -> str:
    """Truncate a chat response to a short summary for the diagnostic prompt."""
    cleaned = re.sub(r"<[^>]+>", "", text)
    # Strip markdown bold/italic
    cleaned = re.sub(r"\*+([^*]+)\*+", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    # Take first sentence if it fits
    sentence_end = re.search(r"[.!?](?:\s|$)", cleaned)
    if sentence_end and sentence_end.start() < max_len:
        return cleaned[: sentence_end.start() + 1]
    if len(cleaned) <= max_len:
        return cleaned
    # Truncate at word boundary
    truncated = cleaned[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated + "\u2026"


def _format_recency(seconds_ago: float) -> str:
    """Format a time delta as a human-readable recency string."""
    if seconds_ago < 60:
        return "just now"
    if seconds_ago < 3600:
        minutes = int(seconds_ago / 60)
        return f"{minutes}m ago"
    hours = int(seconds_ago / 3600)
    return f"{hours}h ago"


@dataclass
class ConversationAssignment:
    """Result of a conversation classification."""

    conversation_id: str
    is_new: bool
    topic_summary: str


class DiagnosticAgent:
    """Lightweight diagnostic agent for conversation classification.

    Uses a haiku-tier LLM to classify each request into a conversation
    thread. The result updates the ConversationTracker so that request
    logs are grouped by topic rather than by idle timeout.
    """

    def __init__(self, llm_client: LLMClient, tracker: ConversationTracker) -> None:
        self._client = llm_client
        self._tracker = tracker

    async def assign_conversation(self, user_input: str) -> Optional[ConversationAssignment]:
        """Classify a user request into a conversation thread.

        Returns None if the agent is disabled, fails, or returns unparseable output.
        """
        if not is_diagnostic_agent_enabled():
            return None

        threads = self._tracker.get_active_threads()
        user_prompt = self._build_user_prompt(user_input, threads)
        response = await self._client.completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        record_cost_from_usage(agent="Diagnostic", model=self._client.model, usage=response.usage)
        return self._parse_response(response.text)

    def apply_assignment(self, assignment: ConversationAssignment) -> None:
        """Apply a conversation assignment to the tracker.

        If the assignment references a new conversation, mints it.
        If it references an existing one, reassigns the current conversation.
        """
        if assignment.is_new:
            # Mint the new conversation in the tracker
            conv_id = self._tracker.new_conversation()
            self._tracker.update_topic(conv_id, assignment.topic_summary)
            assignment.conversation_id = conv_id
        else:
            self._tracker.reassign_current(
                assignment.conversation_id, assignment.topic_summary,
            )

    def _build_user_prompt(
        self, user_input: str, threads: List[ConversationThread],
    ) -> str:
        now = self._tracker.now
        parts = [f"User message: {user_input}", "", "Active conversations (most recent first):"]
        if not threads:
            parts.append("  (none — this will be the first conversation)")
        else:
            for i, t in enumerate(threads):
                summary = t.topic_summary or "(no summary yet)"
                seconds_ago = max(0.0, now - t.last_request_timestamp)
                recency = _format_recency(seconds_ago)
                label = " \u2190 most recent" if i == 0 else ""
                parts.append(
                    f"  {t.id}: {summary} ({t.request_count} requests, {recency}){label}"
                )
                if t.last_response_summary:
                    parts.append(f'    Last response: "{t.last_response_summary}"')
        return "\n".join(parts)

    @staticmethod
    def _parse_response(text: Optional[str]) -> Optional[ConversationAssignment]:
        try:
            data = extract_json_object(text)
            if "is_new" not in data and "conversation_id" not in data:
                raise KeyError("Missing both is_new and conversation_id")
            is_new = data.get("is_new", True)
            if is_new and "conversation_id" not in data:
                data["conversation_id"] = "__new__"
            return ConversationAssignment(
                conversation_id=data["conversation_id"],
                is_new=is_new,
                topic_summary=data.get("topic_summary", ""),
            )
        except (ValueError, json.JSONDecodeError, KeyError):
            _log.warning("Failed to parse diagnostic agent response: %s", text)
            return None
