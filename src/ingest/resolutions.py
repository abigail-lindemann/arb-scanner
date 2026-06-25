"""Resolution capture + signal_log updates. §8.5.

When a matched market settles, record how it resolved. Without this the tool
never learns whether a flagged spread was real, and the convergence model
(§12 Phase C) has no ground truth.

Platform resolution shapes differ:
  - Polymarket: closed/resolved flags, outcomePrices collapsing toward 0/1,
    umaResolutionStatus where present.
  - Kalshi: status -> settled/finalized, `result` names the winning side.

The pure detection + consistency helpers below are unit-tested offline; the
DB read/write functions are thin idempotent wrappers around them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg

from ..ingest import polymarket as pm

log = logging.getLogger(__name__)

# A market is treated as resolved when an outcome price is within this of 0/1.
_COLLAPSE_EPS = 0.02
_KALSHI_SETTLED = {"settled", "finalized", "closed", "determined"}
CONVERGE_EPSILON = 0.02  # spread "converged" if it later drops below this (§8.5)


@dataclass
class Resolution:
    platform: str
    market_id: str
    resolved_outcome: str       # winning outcome label
    final_prob: float           # last observed prob of the winning side
    resolved_at: datetime | None


# --- detection (pure) ------------------------------------------------------

def pm_resolution(market: dict[str, Any]) -> Resolution | None:
    """Detect a settled Polymarket market and its winning outcome.

    Resolved if an explicit closed/resolved flag is set OR an outcome price
    has collapsed to ~1 (with the rest ~0). Returns None if still live.
    """
    closed = bool(market.get("closed") or market.get("resolved"))
    uma = (market.get("umaResolutionStatus") or "").lower()
    pairs = pm.parse_outcome_prices(market)  # [(label, prob), ...]
    if not pairs:
        return None
    winner_label, winner_prob = max(pairs, key=lambda lp: lp[1])
    collapsed = winner_prob >= (1.0 - _COLLAPSE_EPS) and all(
        p <= _COLLAPSE_EPS for l, p in pairs if l != winner_label
    )
    if not (closed or collapsed or uma == "resolved"):
        return None
    return Resolution(
        platform="polymarket",
        market_id=market.get("conditionId") or market.get("slug") or market.get("id"),
        resolved_outcome=winner_label,
        final_prob=winner_prob,
        resolved_at=_parse_ts(market.get("endDate") or market.get("closedTime")),
    )


def kalshi_resolution(market: dict[str, Any]) -> Resolution | None:
    """Detect a settled Kalshi market. `result` is 'yes'/'no' (or a label)."""
    status = (market.get("status") or "").lower()
    result = market.get("result")
    if status not in _KALSHI_SETTLED or not result:
        return None
    label = str(result).strip().capitalize()  # 'yes' -> 'Yes'
    last = market.get("last_price")
    final_prob = (float(last) / 100.0) if last is not None else (
        1.0 if label.lower() == "yes" else 0.0
    )
    return Resolution(
        platform="kalshi",
        market_id=market.get("ticker"),
        resolved_outcome=label,
        final_prob=final_prob,
        resolved_at=_parse_ts(market.get("close_time") or market.get("settled_time")),
    )


def _yes_won(resolved_outcome: str) -> bool:
    return resolved_outcome.strip().lower() in {"yes", "true", "1"}


def outcome_consistent(
    pm_outcome: str,
    kalshi_outcome: str,
    inverted: bool,
) -> bool:
    """Did both legs resolve the same real-world way, accounting for inversion?

    Non-inverted: consistent when both 'Yes' or both 'No'.
    Inverted (PM-Yes ~ Kalshi-No): consistent when they DISAGREE on Yes/No.
    This is the strongest validation that a match was genuine (§8.5).
    """
    pm_yes = _yes_won(pm_outcome)
    k_yes = _yes_won(kalshi_outcome)
    return (pm_yes != k_yes) if inverted else (pm_yes == k_yes)


# --- signal_log derivation (pure) -----------------------------------------

def first_threshold_crossing(
    history: list[tuple[datetime, float]],
    threshold: float = 0.05,
) -> datetime | None:
    """Earliest snapshot ts where net_spread first reached the flag threshold.

    history: (ts, net_spread) in any order; sorted here ascending.
    """
    for ts, net in sorted(history, key=lambda x: x[0]):
        if net is not None and net >= threshold:
            return ts
    return None


def convergence_after(
    history: list[tuple[datetime, float]],
    first_flag_ts: datetime,
    epsilon: float = CONVERGE_EPSILON,
) -> tuple[bool, datetime | None, float | None]:
    """First snapshot at/after first_flag_ts whose net_spread < epsilon.

    Returns (converged, converged_at, days_to_converge). No look-ahead: only
    snapshots from first_flag_ts onward are considered (H11).
    """
    for ts, net in sorted(history, key=lambda x: x[0]):
        if ts < first_flag_ts:
            continue
        if net is not None and net < epsilon:
            days = (ts - first_flag_ts).total_seconds() / 86400.0
            return True, ts, days
    return False, None, None


def _parse_ts(val: Any) -> datetime | None:
    if not val:
        return None
    try:
        from dateutil import parser
        return parser.isoparse(str(val))
    except Exception:
        return None


# --- DB I/O (idempotent) ---------------------------------------------------

def upsert_resolution(conn: psycopg.Connection, res: Resolution) -> None:
    """Insert a resolution; idempotent on (platform, market_id) (§8.5)."""
    sql = (
        "INSERT INTO market_resolutions "
        "(platform, market_id, resolved_outcome, final_prob, resolved_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (platform, market_id) DO NOTHING"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (res.platform, res.market_id, res.resolved_outcome,
                          res.final_prob, res.resolved_at or datetime.now(timezone.utc)))
    conn.commit()


def upsert_signal_log(
    conn: psycopg.Connection,
    pair_id: int,
    category: str,
    first_flagged_at: datetime,
    flagged_net_spread: float,
    status: str,
    converged: bool,
    converged_at: datetime | None,
    days_to_converge: float | None,
    resolved: bool,
    outcome_consistent_flag: bool | None,
) -> None:
    """Upsert one signal_log row, keyed on (pair_id, first_flagged_at)."""
    sql = (
        "INSERT INTO signal_log "
        "(pair_id, category, first_flagged_at, flagged_net_spread, status, "
        " converged, converged_at, days_to_converge, resolved, outcome_consistent) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (pair_id, first_flagged_at) DO UPDATE SET "
        " status=EXCLUDED.status, converged=EXCLUDED.converged, "
        " converged_at=EXCLUDED.converged_at, days_to_converge=EXCLUDED.days_to_converge, "
        " resolved=EXCLUDED.resolved, outcome_consistent=EXCLUDED.outcome_consistent"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (pair_id, category, first_flagged_at, flagged_net_spread,
                          status, converged, converged_at, days_to_converge,
                          resolved, outcome_consistent_flag))
    conn.commit()
