"""Spread snapshots + drastic-move alerts. §8.

Every scanner run writes one spread_snapshots row per matched pair. This is
also the dataset the convergence model (§12 Phase C) trains on, so it is
written from the very first run onward.

After writing, each pair's net_spread is compared to its MOST RECENT PRIOR
snapshot. A jump of >= 0.10 (10 percentage points) queues an alert.

Dedup (per §8 "don't re-alert the same move next run"): because we always
compare against the most recent prior snapshot, a one-off jump registers
exactly once -- on the next run the prior value is already the post-jump
number, so the delta is small and no duplicate fires. A spread that keeps
moving (0.02 -> 0.13 -> 0.25) alerts on each genuinely new step, which is
the intended "moved further" behavior.

The email sender is injected (send_fn) so this module is testable without
SMTP and does not hard-depend on briefing/email_send.py (Phase 8).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import psycopg

log = logging.getLogger(__name__)

ALERT_THRESHOLD = 0.10  # 10 percentage-point jump in net_spread

# send_fn(subject, html_body) -> None
SendFn = Callable[[str, str], None]


@dataclass
class PairSnapshot:
    """Everything needed to (a) persist a snapshot row and (b) render an alert."""
    pair_id: int
    category: str
    pm_mid: float
    kalshi_mid: float
    gross_spread: float
    net_spread: float
    # display-only fields for the alert body
    title: str = ""
    link_pm: str = ""
    link_kalshi: str = ""


@dataclass
class Alert:
    pair_id: int
    title: str
    old_net: float
    new_net: float
    delta: float
    snapshot: PairSnapshot

    @property
    def subject(self) -> str:
        arrow = "↑" if self.new_net > self.old_net else "↓"
        return f"\u26a0 Spread move {arrow} {self.delta:+.1%}: {self.title or self.pair_id}"

    def html(self) -> str:
        s = self.snapshot
        return (
            f"<h3>Spread move on pair {self.pair_id}</h3>"
            f"<p><b>{self.title}</b> [{s.category}]</p>"
            f"<p>net spread {self.old_net:.1%} &rarr; {self.new_net:.1%} "
            f"(&Delta; {self.delta:+.1%})</p>"
            f"<p>Polymarket mid {s.pm_mid:.1%} &middot; Kalshi mid {s.kalshi_mid:.1%} "
            f"&middot; gross {s.gross_spread:.1%}</p>"
            f"<p><a href='{s.link_pm}'>Polymarket</a> &middot; "
            f"<a href='{s.link_kalshi}'>Kalshi</a></p>"
        )


# --- persistence -----------------------------------------------------------

_SNAP_COLS = ["ts", "pair_id", "category", "pm_mid", "kalshi_mid",
              "gross_spread", "net_spread"]


def latest_prior_net(
    conn: psycopg.Connection,
    pair_ids: list[int],
) -> dict[int, float]:
    """Most recent existing net_spread per pair, BEFORE this run's insert.

    Call this prior to write_snapshots so the comparison baseline is the
    previous run, not the row we are about to write.
    """
    if not pair_ids:
        return {}
    sql = (
        "SELECT DISTINCT ON (pair_id) pair_id, net_spread "
        "FROM spread_snapshots WHERE pair_id = ANY(%s) "
        "ORDER BY pair_id, ts DESC"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (pair_ids,))
        return {pid: net for pid, net in cur.fetchall() if net is not None}


def write_snapshots(
    conn: psycopg.Connection,
    snapshots: Iterable[PairSnapshot],
    ts: datetime | None = None,
) -> int:
    """Insert one spread_snapshots row per pair. Returns rows written."""
    ts = ts or datetime.now(timezone.utc)
    records: list[tuple[Any, ...]] = [
        (ts, s.pair_id, s.category, s.pm_mid, s.kalshi_mid,
         s.gross_spread, s.net_spread)
        for s in snapshots
    ]
    if not records:
        log.warning("write_snapshots: nothing to write")
        return 0
    placeholders = "(" + ",".join(["%s"] * len(_SNAP_COLS)) + ")"
    sql = f"INSERT INTO spread_snapshots ({','.join(_SNAP_COLS)}) VALUES {placeholders}"
    with conn.cursor() as cur:
        cur.executemany(sql, records)
    conn.commit()
    log.info("spread_snapshots: wrote %s rows at %s", len(records), ts.isoformat())
    return len(records)


# --- alert detection -------------------------------------------------------

def detect_alerts(
    snapshots: Iterable[PairSnapshot],
    prior_net: dict[int, float],
    threshold: float = ALERT_THRESHOLD,
) -> list[Alert]:
    """Flag pairs whose net_spread jumped >= threshold vs the prior snapshot.

    Pairs with no prior snapshot (brand new) are not alerted -- there is no
    move to measure yet.
    """
    alerts: list[Alert] = []
    for s in snapshots:
        prev = prior_net.get(s.pair_id)
        if prev is None:
            continue
        delta = s.net_spread - prev
        if abs(delta) >= threshold:
            alerts.append(Alert(
                pair_id=s.pair_id, title=s.title,
                old_net=prev, new_net=s.net_spread, delta=delta, snapshot=s,
            ))
    log.info("detect_alerts: %s drastic moves (threshold=%.2f)", len(alerts), threshold)
    return alerts


def dispatch_alerts(alerts: list[Alert], send_fn: SendFn) -> int:
    """Send each alert via the injected send_fn. Fail-soft per alert."""
    sent = 0
    for a in alerts:
        try:
            send_fn(a.subject, a.html())
            sent += 1
        except Exception as e:  # one bad send must not kill the run
            log.warning("alert send failed for pair %s: %s", a.pair_id, e)
    return sent
