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
