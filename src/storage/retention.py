"""Storage retention & rollup. §6.5.

Neon's free tier is ~0.5 GB. Writing ~600 markets_raw rows and hundreds of
spread_snapshots rows every 4h fills that in months, so retention is a v1
requirement.

Policy (windows tunable, H9):
  - markets_raw:       keep ~14 days, then delete (audit trail, not training).
  - spread_snapshots:  keep ~30 days at full 4-hourly resolution. Older than
                       that, roll up to ONE row per pair per day
                       (open/close/min/max net) into spread_snapshots_daily,
                       then delete the fine-grained rows. The daily rollup is
                       tiny and kept forever -- it preserves the spread
                       trajectory the convergence model needs.
  - signal_log, market_resolutions, spread_snapshots_daily: keep forever.

SAFETY (H9): deletion is irreversible. This runs in DRY-RUN mode by default:
it logs what WOULD be rolled up / deleted and changes nothing. A human must
confirm the windows AND that the rollup preserves every convergence feature
before calling run_retention(dry_run=False).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import psycopg

log = logging.getLogger(__name__)

MARKETS_RAW_DAYS = 14
SNAPSHOTS_FULL_DAYS = 30


@dataclass
class DailyRollup:
    day: datetime          # date (midnight) of the rolled-up day
    pair_id: int
    category: str
    net_open: float
    net_close: float
    net_min: float
    net_max: float


@dataclass
class RetentionReport:
    dry_run: bool
    markets_raw_deleted: int = 0
    snapshots_rolled_up: int = 0     # fine-grained rows folded into dailies
    daily_rows_written: int = 0
    snapshots_deleted: int = 0
    rollups: list[DailyRollup] = field(default_factory=list)

    def summary(self) -> str:
        verb = "WOULD" if self.dry_run else "DID"
        return (
            f"[retention {'DRY-RUN' if self.dry_run else 'LIVE'}] {verb}: "
            f"delete {self.markets_raw_deleted} markets_raw, "
            f"roll up {self.snapshots_rolled_up} snapshots into "
            f"{self.daily_rows_written} daily rows, "
            f"delete {self.snapshots_deleted} fine-grained snapshots."
        )


def cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def rollup_rows(
    rows: Iterable[tuple[int, str, datetime, float]],
) -> list[DailyRollup]:
    """Aggregate fine-grained snapshots into one row per (pair_id, day).

    rows: (pair_id, category, ts, net_spread). Pure -- no DB. open/close are
    the first/last net by timestamp within the day; min/max over the day.
    This is the function H9 asks a human to confirm preserves the features
    the convergence model consumes.
    """
    groups: dict[tuple[int, Any], list[tuple[datetime, str, float]]] = defaultdict(list)
    for pair_id, category, ts, net in rows:
        if net is None:
            continue
        day = ts.astimezone(timezone.utc).date()
        groups[(pair_id, day)].append((ts, category, net))

    out: list[DailyRollup] = []
    for (pair_id, day), items in groups.items():
        items.sort(key=lambda x: x[0])
        nets = [n for _, _, n in items]
        out.append(DailyRollup(
            day=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
            pair_id=pair_id,
            category=items[0][1],
            net_open=items[0][2],
            net_close=items[-1][2],
            net_min=min(nets),
            net_max=max(nets),
        ))
    return out


# --- orchestration ---------------------------------------------------------

def run_retention(
    conn: psycopg.Connection,
    dry_run: bool = True,
    now: datetime | None = None,
) -> RetentionReport:
    """Apply (or, by default, simulate) the retention policy.

    With dry_run=True (default) NOTHING is mutated: the report records what
    would happen. Flip to dry_run=False only after a human signs off (H9).
    """
    now = now or datetime.now(timezone.utc)
    raw_cut = cutoff(now, MARKETS_RAW_DAYS)
    snap_cut = cutoff(now, SNAPSHOTS_FULL_DAYS)
    report = RetentionReport(dry_run=dry_run)

    with conn.cursor() as cur:
        # 1) markets_raw older than the window.
        cur.execute("SELECT count(*) FROM markets_raw WHERE ts < %s", (raw_cut,))
        report.markets_raw_deleted = cur.fetchone()[0]

        # 2) fine-grained snapshots older than the window -> roll up.
        cur.execute(
            "SELECT pair_id, category, ts, net_spread FROM spread_snapshots "
            "WHERE ts < %s ORDER BY pair_id, ts",
            (snap_cut,),
        )
        old_snaps = cur.fetchall()
        report.snapshots_rolled_up = len(old_snaps)
        report.rollups = rollup_rows(old_snaps)
        report.daily_rows_written = len(report.rollups)
        report.snapshots_deleted = len(old_snaps)

        if dry_run:
            log.info(report.summary())
            return report

        # LIVE path -- only reached after explicit dry_run=False (H9).
        for r in report.rollups:
            cur.execute(
                "INSERT INTO spread_snapshots_daily "
                "(day, pair_id, category, net_open, net_close, net_min, net_max) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (day, pair_id) DO UPDATE SET "
                " net_open=EXCLUDED.net_open, net_close=EXCLUDED.net_close, "
                " net_min=EXCLUDED.net_min, net_max=EXCLUDED.net_max",
                (r.day, r.pair_id, r.category, r.net_open, r.net_close,
                 r.net_min, r.net_max),
            )
        cur.execute("DELETE FROM spread_snapshots WHERE ts < %s", (snap_cut,))
        cur.execute("DELETE FROM markets_raw WHERE ts < %s", (raw_cut,))
    conn.commit()
    log.info(report.summary())
    return report
