"""Dev utility: seed messages.db with synthetic multi-day history.

Populates the persistent message store with several days of chat + diagnostics
so the lazy-load / history-browsing UI can be exercised without a real
long-running instance. Includes deliberate gap days (to test skip-empty), a
shared request_id per turn (to test the chat<->diagnostics request sync), and a
mix of successful and failed requests so the Agents, Tools, Errors and Session
Requests diagnostics panels all have entries.

Run against the instance's data dir, e.g.:
    ./dev python bench/seed_history.py            # default spread
    ./dev python bench/seed_history.py --days 0 1 3 8   # active days-ago

Also seeds the cost ledger (Coordinator per request, plus a daily Analyser and
Diagnostic charge on distinct models) so the cost dashboard has spread across
agents, models, conversations and days.

Clears ws_messages + cost_ledger first, then inserts — re-running gives the same
clean set, no duplicates. Writes to $SWARPIUS_DATA_DIR/messages.db (default
agent/data/messages.db).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_paths import ensure_dirs, messages_db_path  # noqa: E402
from app.io.state_db import StateDb  # noqa: E402
from app.settings.env_file import load_env_into_process  # noqa: E402

# Resolve the same data dir the agent uses (honours SWARPIUS_DATA_DIR in .env).
load_env_into_process()

# Active days-ago to populate (gaps between them exercise skip-empty).
_DEFAULT_DAYS_AGO = [0, 1, 4, 9]
_TURNS_PER_DAY = 4  # the last turn of each day is a failed request
_MODEL = "anthropic/claude-sonnet-4-6"
# Distinct models per agent so the cost dashboard's by-model breakdown has spread.
_ANALYSER_MODEL = "anthropic/claude-opus-4-8"
_DIAGNOSTIC_MODEL = "anthropic/claude-haiku-4-5"
_PROMPTS = [
    ("play some miles davis", "Playing Kind of Blue in the Kitchen."),
    ("what's playing", "Kind of Blue — Miles Davis, in the Kitchen."),
    ("skip this track", "Skipped to Blue in Green."),
    ("turn it down a bit", "Volume set to 35% in the Kitchen."),
    ("queue some coltrane", "Added A Love Supreme to the queue."),
]
_FAILURES = [
    ("play my road-trip playlist", "Roon Core returned no matching results after 3 retries."),
    ("send it to the patio", "Transfer failed: zone 'Patio' not found."),
]


def _ts_ms(days_ago: int, hour: int, minute: int = 0) -> int:
    d = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    return int(d.timestamp() * 1000)


def _insert(conn, channel: str, payload: dict, created_at: int, meta: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO ws_messages (channel, payload, meta, created_at) VALUES (?, ?, ?, ?)",
        (channel, json.dumps(payload), json.dumps(meta) if meta else None, created_at),
    )


def _insert_cost(conn, *, agent: str, model: str, cost_usd: float,
                 input_tokens: int, output_tokens: int, ts: int,
                 request_id: str | None = None, conversation_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO cost_ledger (ts, agent, model, request_id, conversation_id, "
        "input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, cost_usd) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, agent, model, request_id, conversation_id,
         input_tokens, output_tokens, 0, 0, round(cost_usd, 4)),
    )


def _seed_request(conn, rid: str, cmid: str, base: int, user_text: str,
                  agent_text: str, conv_id: str) -> int:
    """A successful request: chat Q+A plus the agent-outputs lifecycle
    (request_id_assignment → coordinator_step → response → request_complete)
    and a tool call. Appears in Chat, Agents, Tools and Session Requests."""
    _insert(conn, "chat", {"channel": "chat", "body": user_text},
            base, {"direction": "outbound", "client_msg_id": cmid})
    _insert(conn, "agent-outputs",
            {"event_type": "request_id_assignment", "source": "[Request]",
             "text": f"Request {rid}: {user_text}", "user_input": user_text,
             "request_id": rid, "client_msg_id": cmid, "coordinator_model": _MODEL,
             "timestamp_ms": base},
            base + 100)
    _insert(conn, "agent-outputs",
            {"event_type": "coordinator_step", "source": "[Coordinator]",
             "request_id": rid, "step": 1, "selected_skill": "roon_action",
             "done": False, "duration_ms": 900},
            base + 600)
    _insert(conn, "tool-outputs",
            {"source": "Roon", "event_type": "tool_complete", "request_id": rid,
             "summary": "roon_action"},
            base + 1500)
    _insert(conn, "agent-outputs",
            {"source": "[Response]", "event_type": "response", "text": agent_text,
             "request_id": rid},
            base + 2400)
    _insert(conn, "chat",
            {"agent_name": "Coordinator", "chat_response": agent_text, "request_id": rid},
            base + 2500, {"agent_name": "Coordinator", "request_id": rid})
    _insert(conn, "agent-outputs",
            {"source": "[Request Complete]", "event_type": "request_complete",
             "request_id": rid, "total_steps": 1, "total_duration_ms": 2500,
             "status": "ok", "coordinator_model": _MODEL, "conversation_id": conv_id},
            base + 2600)
    # The Coordinator cost for this request (varies with prompt length).
    _insert_cost(conn, agent="Coordinator", model=_MODEL,
                 cost_usd=0.006 + len(user_text) * 0.0004,
                 input_tokens=3200 + len(user_text) * 20, output_tokens=240,
                 ts=base + 2600, request_id=rid, conversation_id=conv_id)
    return 7


def _seed_failed(conn, rid: str, cmid: str, base: int, user_text: str,
                 error_text: str) -> int:
    """A failed request: the user message (which renders a failed pill in chat),
    its start event, a tool call, and an errors-channel entry. Appears in Chat,
    Agents, Tools and Errors. No request_complete — matching the real flow."""
    _insert(conn, "chat", {"channel": "chat", "body": user_text},
            base, {"direction": "outbound", "client_msg_id": cmid})
    _insert(conn, "agent-outputs",
            {"event_type": "request_id_assignment", "source": "[Request]",
             "text": f"Request {rid}: {user_text}", "user_input": user_text,
             "request_id": rid, "client_msg_id": cmid, "coordinator_model": _MODEL,
             "timestamp_ms": base},
            base + 100)
    _insert(conn, "tool-outputs",
            {"source": "Roon", "event_type": "tool_complete", "request_id": rid,
             "summary": "roon_action"},
            base + 1500)
    _insert(conn, "errors",
            {"source": "[Request]", "error": error_text, "request_id": rid},
            base + 2000)
    _insert(conn, "llm-diagnostics",
            {"event_type": "call_failed", "call_id": rid, "request_id": rid,
             "error": error_text},
            base + 2000)
    return 5


def seed(days_ago: list[int]) -> int:
    ensure_dirs()
    db = StateDb(messages_db_path())
    inserted = 0
    conv = 0
    try:
        with db.transaction() as conn:
            conn.execute("DELETE FROM ws_messages")  # clean slate — no duplicates on re-run
            conn.execute("DELETE FROM cost_ledger")
            for day in sorted(days_ago, reverse=True):
                conv += 1
                conv_id = f"c{conv:02d}"
                for turn in range(_TURNS_PER_DAY):
                    rid = f"rq-{conv_id}-{turn + 1:04d}"
                    cmid = f"seed-{conv_id}-{turn + 1}"
                    base = _ts_ms(day, 9 + turn)
                    if turn == _TURNS_PER_DAY - 1:
                        user_text, error_text = _FAILURES[conv % len(_FAILURES)]
                        inserted += _seed_failed(conn, rid, cmid, base, user_text, error_text)
                    else:
                        user_text, agent_text = _PROMPTS[(conv + turn) % len(_PROMPTS)]
                        inserted += _seed_request(conn, rid, cmid, base, user_text, agent_text, conv_id)
                # Sub-agent / analyser spend for the day (no conversation id —
                # these group under the dashboard's unattributed bucket).
                day_end = _ts_ms(day, 23)
                _insert_cost(conn, agent="Analyser", model=_ANALYSER_MODEL,
                             cost_usd=0.12 + (conv % 3) * 0.06,
                             input_tokens=42000, output_tokens=1800, ts=day_end)
                _insert_cost(conn, agent="Diagnostic", model=_DIAGNOSTIC_MODEL,
                             cost_usd=0.0004, input_tokens=900, output_tokens=40,
                             ts=day_end + 1000)
    finally:
        db.close()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic message history.")
    parser.add_argument(
        "--days", type=int, nargs="+", default=_DEFAULT_DAYS_AGO,
        help="Active days-ago to populate (default: %(default)s).",
    )
    args = parser.parse_args()
    path = messages_db_path()
    print(f"Clearing + seeding history in {path} for days-ago {sorted(args.days, reverse=True)} …")
    n = seed(args.days)
    print(f"Cleared existing messages + cost ledger, inserted {n} messages plus cost rows "
          f"across {len(set(args.days))} day(s). Restart/reconnect the agent to see them.")


if __name__ == "__main__":
    main()
