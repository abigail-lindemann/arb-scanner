"""Scanner entrypoint: the full pipeline, run by GitHub Actions every 4h. §9.1.

Flow (each step fail-soft per §0.5):
  fetch PM + Kalshi -> normalize -> write markets_raw
    -> embedding candidates -> align uncached via Haiku -> score/confidence
    -> persist matched_pairs
    -> enrich matched markets' executable bid/ask (PM CLOB)
    -> compute net spreads (mid for the scatter, bid/ask for the tables)
    -> write spread_snapshots + drastic-move alerts
    -> capture resolutions + update signal_log
    -> retention (DRY-RUN by default, H9)
    -> write docs/data.json

The browser only ever reads docs/data.json; it holds no secrets and calls no
APIs (§0 rule 4). The GitHub Action commits data.json back to the repo.

`build_data_json` is a pure function (no I/O) so it is unit-tested offline;
`main` does the live wiring and is exercised only in Actions.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_JSON = Path("docs/data.json")
PM_MARKET_URL = "https://polymarket.com/event/"
KALSHI_MARKET_URL = "https://kalshi.com/markets/"


@dataclass
class EventRow:
    """One row in data.json -> one matched, scored, priced pair."""
    pair_id: int
    title_pm: str
    title_kalshi: str
    category: str
    pm_mid: float
    kalshi_mid: float
    pm_bidask: list[float | None]
    kalshi_bidask: list[float | None]
    gross_spread: float
    net_spread: float
    est_edge_usd: float
    confidence: float
    inverted: bool
    resolution_warning: bool
    volume_pm: float | None
    volume_kalshi: float | None
    end_date: str | None
    link_pm: str
    link_kalshi: str
    spread_history: list[dict[str, Any]]


def build_data_json(events: list[EventRow], generated_at: datetime | None = None) -> dict:
    """Assemble the data.json payload from scored event rows. Pure."""
    generated_at = generated_at or datetime.now(timezone.utc)
    return {
        "generated_at": generated_at.isoformat(),
        "count": len(events),
        "events": [asdict(e) for e in events],
    }


def _json_safe(obj):
    """Recursively replace NaN / Infinity (which are NOT valid JSON and break
    the browser's JSON.parse) with None. Mirrors the fail-loud philosophy from
    the one-sided-feed guard: a non-finite float should never silently produce
    an unparseable file."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def write_data_json(payload: dict, path: Path = DATA_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False is a belt-and-suspenders guard: if any non-finite float
    # somehow survives _json_safe, dump raises loudly instead of writing a
    # broken file that fetches with 200 but fails res.json() in the browser.
    path.write_text(json.dumps(_json_safe(payload), indent=2, allow_nan=False))
    log.info("wrote %s (%s events)", path, payload.get("count"))


def _kalshi_volume(row: dict) -> float | None:
    """Kalshi migrated volume_24h -> volume_24h_fp. Read the new field, fall
    back to the old one, and never return NaN."""
    v = row.get("volume_24h_fp")
    if v is None:
        v = row.get("volume_24h")
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def main() -> None:  # pragma: no cover - live wiring, exercised in Actions
    """Live run. Imports heavy/credentialed deps lazily so the module stays
    importable (and testable) without them."""
    logging.basicConfig(level=logging.INFO)
    import anthropic

    from .arb.spread import compute_spread
    from .ingest import kalshi, polymarket, resolutions
    from .ingest.normalize import build_unified, unmapped_categories
    from .match.align_agent import align_candidates
    from .match.confidence import score_pair
    from .match.embed import candidate_pairs
    from .ml.snapshots import PairSnapshot, detect_alerts, dispatch_alerts, \
        latest_prior_net, write_snapshots
    from .storage import db, writes
    from .storage.retention import run_retention

    ts = datetime.now(timezone.utc)
    conn = db.connect()
    db.init_schema(conn)

    # 1) ingest + normalize
    pm_markets = polymarket.fetch_markets(top_n=300)
    kalshi_markets = kalshi.fetch_markets(top_n=300)
    df = build_unified(pm_markets, kalshi_markets)
    writes.write_markets_raw(conn, df, ts)
    unmapped = unmapped_categories(df)
    if unmapped:
        log.warning("H2: %s unmapped categories using conservative fee: %s",
                    len(unmapped), sorted(unmapped))

    # 2) match: embeddings -> align uncached -> confidence
    cands = candidate_pairs(df)
    cached = writes.load_cached_pair_keys(conn)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    fresh = align_candidates(client, cands, cached_keys=cached)
    cand_by_key = {(c.pm_market_id, c.kalshi_market_id): c for c in cands}
    for key, alignment in fresh.items():
        c = cand_by_key[key]
        sp = score_pair(c, alignment)
        writes.upsert_matched_pair(
            conn, c.pm_market_id, c.kalshi_market_id, c.embedding_sim,
            alignment.outcome_map, alignment.inverted,
            alignment.resolution_match_score, alignment.same_event,
            sp.confidence, ts)

    # 3) assemble priced events from ALL stored pairs (fresh + cached).
    # Index this run's rows for titles/volumes/bid-ask by market_id.
    pm_by_id = {r["market_id"]: r for r in df[df["platform"] == "polymarket"].to_dict("records")}
    ks_by_id = {r["market_id"]: r for r in df[df["platform"] == "kalshi"].to_dict("records")}
    pm_token = {m.get("conditionId") or m.get("slug") or m.get("id"):
                polymarket.yes_token_id(m) for m in pm_markets}

    events: list[EventRow] = []
    snapshots: list[PairSnapshot] = []
    for p in writes.load_matched_pairs(conn):
        prow = pm_by_id.get(p["pm_market_id"])
        krow = ks_by_id.get(p["kalshi_market_id"])
        if not prow or not krow:
            continue  # one leg not in the current top-300 feed this run
        try:
            # executable bid/ask for the actionable view (matched markets only)
            tok = pm_token.get(p["pm_market_id"])
            pm_ba = polymarket.enrich_bidask(tok) if tok else {}
            pm_mid = pm_ba.get("prob_mid") or prow["prob_mid"]
            pm_ask = pm_ba.get("prob_yes_ask")
            k_mid = krow["prob_mid"]
            k_ask = krow["prob_yes_ask"]

            # scatter/agreement uses mid; tables use executable bid/ask.
            mid_res = compute_spread(pm_mid, k_mid, p["category"] if "category" in p else prow["category"],
                                     inverted=p["inverted"],
                                     resolution_match_score=p["resolution_match_score"])
            exec_res = compute_spread(pm_ask if pm_ask is not None else pm_mid,
                                      k_ask if k_ask is not None else k_mid,
                                      prow["category"], inverted=p["inverted"],
                                      resolution_match_score=p["resolution_match_score"])

            snapshots.append(PairSnapshot(
                pair_id=p["id"], category=prow["category"],
                pm_mid=pm_mid, kalshi_mid=k_mid,
                gross_spread=mid_res.gross_spread, net_spread=exec_res.net_spread,
                title=prow["title"],
                link_pm=PM_MARKET_URL + str(p["pm_market_id"]),
                link_kalshi=KALSHI_MARKET_URL + str(p["kalshi_market_id"]),
            ))
            events.append(EventRow(
                pair_id=p["id"], title_pm=prow["title"], title_kalshi=krow["title"],
                category=prow["category"], pm_mid=pm_mid, kalshi_mid=k_mid,
                pm_bidask=[pm_ba.get("prob_yes_bid"), pm_ask],
                kalshi_bidask=[krow["prob_yes_bid"], krow["prob_yes_ask"]],
                gross_spread=mid_res.gross_spread, net_spread=exec_res.net_spread,
                est_edge_usd=exec_res.est_edge_usd, confidence=p["confidence"],
                inverted=p["inverted"], resolution_warning=exec_res.resolution_warning,
                volume_pm=prow["volume_24h"], volume_kalshi=_kalshi_volume(krow),
                end_date=prow["end_date"],
                link_pm=PM_MARKET_URL + str(p["pm_market_id"]),
                link_kalshi=KALSHI_MARKET_URL + str(p["kalshi_market_id"]),
                spread_history=writes.recent_spread_history(conn, p["id"]),
            ))
        except Exception as e:
            log.warning("pricing pair %s failed: %s", p["id"], e)

    # 4) snapshots + drastic-move alerts
    prior = latest_prior_net(conn, [s.pair_id for s in snapshots])
    write_snapshots(conn, snapshots, ts)
    alerts = detect_alerts(snapshots, prior)
    if alerts:
        from .briefing.email_send import send_email
        dispatch_alerts(alerts, send_email)

    # 5) resolution capture (settled markets among matched pairs)
    for m in pm_markets:
        try:
            r = resolutions.pm_resolution(m)
            if r:
                resolutions.upsert_resolution(conn, r)
        except Exception as e:
            log.warning("pm resolution failed: %s", e)
    for m in kalshi_markets:
        try:
            r = resolutions.kalshi_resolution(m)
            if r:
                resolutions.upsert_resolution(conn, r)
        except Exception as e:
            log.warning("kalshi resolution failed: %s", e)

    # 6) write the dashboard payload, then dry-run retention (H9)
    write_data_json(build_data_json(events, ts))
    run_retention(conn, dry_run=True, now=ts)
    conn.close()
    log.info("scanner run complete: %s events", len(events))


if __name__ == "__main__":  # pragma: no cover
    main()
