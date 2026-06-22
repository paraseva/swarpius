"""Cost ledger: one row per LLM agent invocation, aggregated for the cost
dashboard.

Every LLM consumer (the coordinator, the interrupt arbiter, the diagnostic
agent, the analyser) records its cost here, so the dashboard can break spend
down by agent, model, conversation, and day. Backed by the shared ``StateDb``
(the ``cost_ledger`` table, schema v2); never pruned — rows are tiny and kept
indefinitely. ``record`` must never break a request, so callers use the
module-level :func:`record_cost`, which swallows and logs any failure.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.io.state_db import StateDb

logger = logging.getLogger(__name__)

# Sum expressions shared by the total + every grouped query, in a fixed order.
_SUMS = (
    "COALESCE(SUM(cost_usd), 0), "
    "COALESCE(SUM(input_tokens), 0), "
    "COALESCE(SUM(output_tokens), 0), "
    "COALESCE(SUM(cache_creation_tokens), 0), "
    "COALESCE(SUM(cache_read_tokens), 0), "
    "COUNT(*)"
)


def _metrics(row: Any) -> Dict[str, Any]:
    return {
        "cost_usd": row[0],
        "input_tokens": row[1],
        "output_tokens": row[2],
        "cache_creation_tokens": row[3],
        "cache_read_tokens": row[4],
        "count": row[5],
    }


class CostLedger:
    """Records LLM-call costs and aggregates them over the shared StateDb."""

    def __init__(self, state_db: StateDb) -> None:
        self._db = state_db

    def record(
        self,
        *,
        agent: str,
        model: str,
        cost_usd: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        request_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        ts: Optional[int] = None,
    ) -> None:
        ts_ms = ts if ts is not None else int(time.time() * 1000)
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO cost_ledger ("
                "ts, agent, model, request_id, conversation_id, "
                "input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, cost_usd"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts_ms, agent, model, request_id, conversation_id,
                    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                    float(cost_usd) if cost_usd is not None else 0.0,
                ),
            )
            self._db.conn.commit()

    def aggregate(
        self,
        *,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
        agent: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Totals + breakdowns by agent, model, and local day, over the rows
        matching the optional time range / agent / model filters. The FE picks
        the breakdown to show and narrows via the filters."""
        where: List[str] = []
        params: List[Any] = []
        if since_ms is not None:
            where.append("ts >= ?")
            params.append(since_ms)
        if until_ms is not None:
            where.append("ts < ?")
            params.append(until_ms)
        if agent is not None:
            where.append("agent = ?")
            params.append(agent)
        if model is not None:
            where.append("model = ?")
            params.append(model)
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        def grouped(expr: str, order_desc: str = "SUM(cost_usd) DESC") -> List[Dict[str, Any]]:
            rows = self._db.conn.execute(
                f"SELECT {expr} AS key, {_SUMS} FROM cost_ledger{clause} "
                f"GROUP BY key ORDER BY {order_desc}",
                params,
            ).fetchall()
            return [{"key": r[0], **_metrics(r[1:])} for r in rows]

        with self._db.lock:
            total = _metrics(
                self._db.conn.execute(f"SELECT {_SUMS} FROM cost_ledger{clause}", params).fetchone(),
            )
            by_agent = grouped("agent")
            by_model = grouped("model")
            # Local-day buckets, oldest first (for a trend line).
            by_day = grouped(
                "strftime('%Y-%m-%d', ts / 1000, 'unixepoch', 'localtime')",
                order_desc="key ASC",
            )
        return {"total": total, "by_agent": by_agent, "by_model": by_model, "by_day": by_day}


class NullCostLedger:
    """No-op ledger for CLI/tests before the DB is wired (and as the default)."""

    def record(self, **kwargs: Any) -> None:
        pass

    def aggregate(self, **kwargs: Any) -> Dict[str, Any]:
        return {"total": _metrics((0, 0, 0, 0, 0, 0)), "by_agent": [], "by_model": [], "by_day": []}


# Module-level singleton — callers use get/set, never import the instance.
_ledger: Any = NullCostLedger()


def get_cost_ledger() -> Any:
    return _ledger


def set_cost_ledger(ledger: Any) -> None:
    global _ledger
    _ledger = ledger


def record_cost(**kwargs: Any) -> None:
    """Record a cost row, swallowing any error — a ledger write must never
    break the request it is measuring."""
    try:
        _ledger.record(**kwargs)
    except Exception:  # noqa: BLE001 — cost accounting is best-effort
        logger.warning("Failed to record cost", exc_info=True)


def record_cost_from_usage(
    *,
    agent: str,
    model: str,
    usage: Optional[Dict[str, Any]],
    request_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    ts: Optional[int] = None,
) -> None:
    """Record a cost row from an ``LLMResponse.usage`` dict (mapping its
    ``*_input_tokens`` cache keys to the ledger's columns). Used by every LLM
    consumer so the key mapping lives in one place."""
    usage = usage or {}
    record_cost(
        agent=agent,
        model=model,
        cost_usd=usage.get("cost_usd"),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        request_id=request_id,
        conversation_id=conversation_id,
        ts=ts,
    )
