"""Snapshot writers for the Postgres tables. Kept separate from connection
management (db.py) and from the pure transform logic (normalize.py).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import psycopg

log = logging.getLogger(__name__)

_MARKETS_RAW_COLS = [
    "ts", "platform", "market_id", "event_id", "title", "description",
    "category", "outcomes", "prob_mid", "prob_yes_bid", "prob_yes_ask",
    "volume_24h", "liquidity", "end_date", "status",
]


def write_markets_raw(
    conn: psycopg.Connection,
    df: pd.DataFrame,
    ts: datetime | None = None,
) -> int:
    """Insert every unified row as a markets_raw snapshot at timestamp `ts`.

    Returns the number of rows written. JSON-encodes the `outcomes` list.
    """
    ts = ts or datetime.now(timezone.utc)
    records: list[tuple[Any, ...]] = []
    for r in df.to_dict("records"):
        records.append((
            ts, r["platform"], r["market_id"], r["event_id"], r["title"],
            r["description"], r["category"], json.dumps(r["outcomes"]),
            r["prob_mid"], r["prob_yes_bid"], r["prob_yes_ask"],
            r["volume_24h"], r["liquidity"], r["end_date"] or None, r["status"],
        ))
    if not records:
        log.warning("write_markets_raw: nothing to write")
        return 0
    placeholders = "(" + ",".join(["%s"] * len(_MARKETS_RAW_COLS)) + ")"
    sql = (
        f"INSERT INTO markets_raw ({','.join(_MARKETS_RAW_COLS)}) "
        f"VALUES {placeholders}"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, records)
    conn.commit()
    log.info("markets_raw: wrote %s rows at %s", len(records), ts.isoformat())
    return len(records)


def upsert_matched_pair(
    conn: psycopg.Connection,
    pm_market_id: str,
    kalshi_market_id: str,
    embedding_sim: float,
    outcome_map: dict | None,
    inverted: bool,
    resolution_match_score: float,
    same_event: bool,
    confidence: float,
    ts: datetime,
) -> None:
    """Insert or update a matched pair, bumping last_seen on conflict."""
    sql = """
        INSERT INTO matched_pairs
            (pm_market_id, kalshi_market_id, embedding_sim, outcome_map,
             inverted, resolution_match_score, same_event, confidence,
             created_at, last_seen)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (pm_market_id, kalshi_market_id) DO UPDATE SET
            embedding_sim = EXCLUDED.embedding_sim,
            outcome_map = EXCLUDED.outcome_map,
            inverted = EXCLUDED.inverted,
            resolution_match_score = EXCLUDED.resolution_match_score,
            same_event = EXCLUDED.same_event,
            confidence = EXCLUDED.confidence,
            last_seen = EXCLUDED.last_seen
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            pm_market_id, kalshi_market_id, embedding_sim,
            json.dumps(outcome_map) if outcome_map is not None else None,
            inverted, resolution_match_score, same_event, confidence,
            ts, ts,
        ))
    conn.commit()


def load_cached_pair_keys(conn: psycopg.Connection) -> set[tuple[str, str]]:
    """Return the set of (pm_market_id, kalshi_market_id) already in matched_pairs.

    Used by the alignment agent to skip re-calling Haiku for pairs it has
    already seen, keeping LLM costs near zero on repeat runs (§5.3).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pm_market_id, kalshi_market_id FROM matched_pairs")
        return {(row[0], row[1]) for row in cur.fetchall()}


def load_matched_pairs(conn: psycopg.Connection) -> list[dict]:
    """Return all rows from matched_pairs as dicts."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, pm_market_id, kalshi_market_id, embedding_sim,
                   outcome_map, inverted, resolution_match_score,
                   same_event, confidence, created_at, last_seen
            FROM matched_pairs
            ORDER BY confidence DESC
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def recent_spread_history(
    conn: psycopg.Connection,
    pair_id: int,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Return the last `limit` spread snapshots for a pair, oldest first.

    Used to draw the sparkline in the KPI detail card.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ts, net_spread
            FROM spread_snapshots
            WHERE pair_id = %s
            ORDER BY ts DESC
            LIMIT %s
        """, (pair_id, limit))
        rows = cur.fetchall()
    # reverse so oldest is first (chronological for the sparkline)
    return [{"ts": row[0].isoformat(), "net": row[1]} for row in reversed(rows)]
