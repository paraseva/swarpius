"""Dev utility: seed messages.db with synthetic multi-day history.

Populates the persistent message store with several days of chat + diagnostics
so the lazy-load / history-browsing UI can be exercised without a real
long-running instance. Includes deliberate gap days (to test skip-empty) and a
shared request_id per turn (to test the chat<->diagnostics badge jump).

Run against the instance's data dir, e.g.:
    ./dev python bench/seed_history.py            # default spread
    ./dev python bench/seed_history.py --days 0 1 3 8   # active days-ago

Additive — it inserts rows; it does not clear. Writes to
$SWARPIUS_DATA_DIR/messages.db (default agent/data/messages.db).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_paths import messages_db_path  # noqa: E402
from app.io.state_db import StateDb  # noqa: E402
from app.settings.env_file import load_env_into_process  # noqa: E402

# Resolve the same data dir the agent uses (honours SWARPIUS_DATA_DIR in .env).
load_env_into_process()

# Active days-ago to populate (gaps between them exercise skip-empty).
_DEFAULT_DAYS_AGO = [0, 1, 4, 9]
_TURNS_PER_DAY = 4
_PROMPTS = [
    ("play some miles davis", "Playing Kind of Blue in the Kitchen."),
    ("what's playing", "Kind of Blue — Miles Davis, in the Kitchen."),
    ("skip this track", "Skipped to Blue in Green."),
    ("turn it down a bit", "Volume set to 35% in the Kitchen."),
    ("queue some coltrane", "Added A Love Supreme to the queue."),
    ("pause", "Paused playback in the Kitchen."),
]


def _ts_ms(days_ago: int, hour: int, minute: int = 0) -> int:
    d = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    return int(d.timestamp() * 1000)


def _insert(conn, channel: str, payload: dict, created_at: int, meta: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO ws_messages (channel, payload, meta, created_at) VALUES (?, ?, ?, ?)",
        (
            channel,
            json.dumps(payload),
            json.dumps(meta) if meta else None,
            created_at,
        ),
    )


def seed(days_ago: list[int]) -> int:
    db = StateDb(messages_db_path())
    inserted = 0
    conv = 0
    try:
        with db.transaction() as conn:
            for day in sorted(days_ago, reverse=True):
                conv += 1
                for turn in range(_TURNS_PER_DAY):
                    user_text, agent_text = _PROMPTS[(conv + turn) % len(_PROMPTS)]
                    rid = f"rq-c{conv:02d}-{turn + 1:04d}"
                    base = _ts_ms(day, 9 + turn)
                    # user chat (outbound), agent reply (chat), plus a couple of
                    # diagnostics events carrying the shared request_id.
                    _insert(conn, "chat", {"channel": "chat", "body": user_text},
                            base, {"direction": "outbound"})
                    _insert(conn, "agent-outputs",
                            {"event_type": "request_started", "request_id": rid},
                            base + 500)
                    _insert(conn, "tool-outputs",
                            {"request_id": rid, "source": "Roon", "summary": "roon_action"},
                            base + 1500)
                    _insert(conn, "chat", {"channel": "chat", "body": agent_text},
                            base + 2500, {"request_id": rid})
                    inserted += 4
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
    print(f"Seeding history into {path} for days-ago {sorted(args.days, reverse=True)} …")
    n = seed(args.days)
    print(f"Inserted {n} messages across {len(set(args.days))} day(s). "
          f"Restart/reconnect the agent to see them.")


if __name__ == "__main__":
    sys.exit(main())
