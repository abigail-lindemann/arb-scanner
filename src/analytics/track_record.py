"""Signal track-record analytics. §9.5.

Answers "when the scanner flagged a >5% net spread, what actually happened?"
Reads signal_log and computes hit rate, days-to-converge distribution,
captured-vs-realized, and match quality, broken down by category and
confidence bucket. Writes docs/track-record.json.

H11 honesty rules baked in:
  - definitions are fixed inputs, not derived from outcomes (no look-ahead);
  - expired/failed signals are counted, never dropped;
  - every bucket reports its sample size.

`compute_metrics` is pure (takes plain rows) so it is unit-tested offline.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TRACK_JSON = Path("docs/track-record.json")
CONFIDENCE_BUCKETS = [(0.80, 1.01, "high"), (0.60, 0.80, "medium"), (0.0, 0.60, "low")]


@dataclass
class SignalRow:
    pair_id: int
    category: str
    confidence: float
    flagged_net_spread: float
    status: str                       # 'open' | 'converged' | 'resolved' | 'expired'
    converged: bool
    days_to_converge: float | None
    resolved: bool
    outcome_consistent: bool | None
    realized_close: float | None = None   # how much the spread actually closed


def _bucket(conf: float) -> str:
    for lo, hi, name in CONFIDENCE_BUCKETS:
        if lo <= conf < hi:
            return name
    return "low"


@dataclass
class GroupMetrics:
    n: int = 0
    converged: int = 0
    resolved: int = 0
    outcome_consistent: int = 0
    days: list[float] = field(default_factory=list)
    captured: list[float] = field(default_factory=list)
    realized: list[float] = field(default_factory=list)

    def add(self, r: SignalRow) -> None:
        self.n += 1
        if r.converged:
            self.converged += 1
            if r.days_to_converge is not None:
                self.days.append(r.days_to_converge)
        if r.resolved:
            self.resolved += 1
            if r.outcome_consistent:
                self.outcome_consistent += 1
        self.captured.append(r.flagged_net_spread)
        if r.realized_close is not None:
            self.realized.append(r.realized_close)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "hit_rate": (self.converged / self.n) if self.n else None,
            "median_days_to_converge": (statistics.median(self.days) if self.days else None),
            "resolved": self.resolved,
            "match_quality": (self.outcome_consistent / self.resolved) if self.resolved else None,
            "avg_captured_spread": (statistics.mean(self.captured) if self.captured else None),
            "avg_realized_close": (statistics.mean(self.realized) if self.realized else None),
        }


def compute_metrics(rows: list[SignalRow]) -> dict[str, Any]:
    """All track-record metrics from signal_log rows. Pure, no I/O."""
    overall = GroupMetrics()
    by_cat: dict[str, GroupMetrics] = {}
    by_conf: dict[str, GroupMetrics] = {}
    status_counts: dict[str, int] = {}
    days_all: list[float] = []

    for r in rows:
        overall.add(r)
        by_cat.setdefault(r.category, GroupMetrics()).add(r)
        by_conf.setdefault(_bucket(r.confidence), GroupMetrics()).add(r)
        status_counts[r.status] = status_counts.get(r.status, 0) + 1
        if r.converged and r.days_to_converge is not None:
            days_all.append(r.days_to_converge)

    return {
        "overall": overall.to_dict(),
        "by_category": {k: v.to_dict() for k, v in sorted(by_cat.items())},
        "by_confidence": {k: by_conf[k].to_dict() for k in ("high", "medium", "low") if k in by_conf},
        # status_counts includes expired/failed signals -- never hidden (H11)
        "status_counts": status_counts,
        "days_to_converge_histogram": _histogram(days_all),
    }


def _histogram(days: list[float], edges=(0, 1, 3, 7, 14, 30)) -> list[dict[str, Any]]:
    bins = [{"from": edges[i], "to": edges[i + 1], "count": 0}
            for i in range(len(edges) - 1)]
    bins.append({"from": edges[-1], "to": None, "count": 0})  # overflow
    for d in days:
        placed = False
        for b in bins[:-1]:
            if b["from"] <= d < b["to"]:
                b["count"] += 1
                placed = True
                break
        if not placed:
            bins[-1]["count"] += 1
    return bins


def write_track_record(metrics: dict[str, Any], path: Path = TRACK_JSON,
                       generated_at: datetime | None = None) -> None:
    generated_at = generated_at or datetime.now(timezone.utc)
    payload = {"generated_at": generated_at.isoformat(), **metrics}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    log.info("wrote %s", path)


def main() -> None:  # pragma: no cover - live DB wiring
    logging.basicConfig(level=logging.INFO)
    from ..storage import db
    conn = db.connect()
    cols = ["pair_id", "category", "confidence", "flagged_net_spread", "status",
            "converged", "days_to_converge", "resolved", "outcome_consistent"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.pair_id, s.category, m.confidence, s.flagged_net_spread, "
            "s.status, s.converged, s.days_to_converge, s.resolved, s.outcome_consistent "
            "FROM signal_log s JOIN matched_pairs m ON m.id = s.pair_id"
        )
        rows = [SignalRow(*row) for row in cur.fetchall()]
    write_track_record(compute_metrics(rows))
    conn.close()


if __name__ == "__main__":  # pragma: no cover
    main()
