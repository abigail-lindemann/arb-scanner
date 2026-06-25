"""Read the paper portfolio from Postgres. §11.

Holdings live in the `portfolio` table, never in the repo (§0 rule 2). If the
table is empty the briefing still runs on market + news only (graceful).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

log = logging.getLogger(__name__)


@dataclass
class Holding:
    ticker: str
    shares: float
    avg_cost: float


def load_portfolio(conn: psycopg.Connection) -> list[Holding]:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, shares, avg_cost FROM portfolio")
        rows = cur.fetchall()
    holdings = [Holding(t, float(s), float(c)) for t, s, c in rows]
    log.info("portfolio: %s holdings", len(holdings))
    return holdings
