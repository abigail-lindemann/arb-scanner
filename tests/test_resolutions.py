"""Offline tests for resolution capture (§8.5) + retention rollup (§6.5).

No DB: exercises the pure detection / consistency / derivation / rollup
helpers. Run: python tests/test_resolutions.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.resolutions import (
    convergence_after,
    first_threshold_crossing,
    kalshi_resolution,
    outcome_consistent,
    pm_resolution,
)
from src.storage.retention import cutoff, rollup_rows

UTC = timezone.utc


# --- resolution detection --------------------------------------------------

def test_pm_resolution_collapsed_prices():
    m = {"conditionId": "0xa", "outcomes": '["Yes","No"]',
         "outcomePrices": '["0.99","0.01"]', "endDate": "2026-01-01T00:00:00Z"}
    r = pm_resolution(m)
    assert r and r.resolved_outcome == "Yes" and r.platform == "polymarket"


def test_pm_resolution_still_live_returns_none():
    m = {"conditionId": "0xb", "outcomes": '["Yes","No"]',
         "outcomePrices": '["0.62","0.38"]'}
    assert pm_resolution(m) is None


def test_pm_resolution_closed_flag():
    m = {"conditionId": "0xc", "closed": True, "outcomes": '["Yes","No"]',
         "outcomePrices": '["0.40","0.60"]'}
    r = pm_resolution(m)
    assert r and r.resolved_outcome == "No"   # winner = higher price


def test_kalshi_resolution_settled():
    m = {"ticker": "T-1", "status": "settled", "result": "yes", "last_price": 100,
         "close_time": "2026-01-01T00:00:00Z"}
    r = kalshi_resolution(m)
    assert r and r.resolved_outcome == "Yes" and abs(r.final_prob - 1.0) < 1e-9


def test_kalshi_resolution_open_returns_none():
    assert kalshi_resolution({"ticker": "T-2", "status": "open"}) is None


# --- outcome consistency (the strongest match validation) ------------------

def test_outcome_consistent_non_inverted():
    assert outcome_consistent("Yes", "Yes", inverted=False) is True
    assert outcome_consistent("Yes", "No", inverted=False) is False


def test_outcome_consistent_inverted():
    # inverted: PM-Yes corresponds to Kalshi-No, so they SHOULD disagree
    assert outcome_consistent("Yes", "No", inverted=True) is True
    assert outcome_consistent("Yes", "Yes", inverted=True) is False


# --- signal_log derivation (no look-ahead) ---------------------------------

def test_first_threshold_crossing():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    hist = [(t0, 0.02), (t0 + timedelta(hours=4), 0.06),
            (t0 + timedelta(hours=8), 0.09)]
    flag = first_threshold_crossing(hist, threshold=0.05)
    assert flag == t0 + timedelta(hours=4)


def test_convergence_after_respects_flag_time():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    flag = t0 + timedelta(hours=4)
    hist = [
        (t0, 0.01),                       # before flag: ignored even though < eps
        (flag, 0.06),
        (t0 + timedelta(hours=8), 0.03),
        (t0 + timedelta(days=1, hours=4), 0.01),  # converges here
    ]
    converged, at, days = convergence_after(hist, flag, epsilon=0.02)
    assert converged and at == t0 + timedelta(days=1, hours=4)
    assert abs(days - 1.0) < 1e-9


def test_convergence_never_returns_false():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    hist = [(t0, 0.08), (t0 + timedelta(hours=4), 0.07)]
    converged, at, days = convergence_after(hist, t0, epsilon=0.02)
    assert converged is False and at is None and days is None


# --- retention rollup ------------------------------------------------------

def test_rollup_open_close_min_max():
    t0 = datetime(2026, 1, 1, 0, tzinfo=UTC)
    rows = [
        (1, "politics", t0 + timedelta(hours=0), 0.05),   # open
        (1, "politics", t0 + timedelta(hours=4), 0.09),   # max
        (1, "politics", t0 + timedelta(hours=8), 0.02),   # min
        (1, "politics", t0 + timedelta(hours=12), 0.06),  # close
        (2, "crypto",   t0 + timedelta(hours=1), 0.03),
    ]
    rollups = {r.pair_id: r for r in rollup_rows(rows)}
    r1 = rollups[1]
    assert (r1.net_open, r1.net_close, r1.net_min, r1.net_max) == (0.05, 0.06, 0.02, 0.09)
    assert rollups[2].net_open == 0.03 and rollups[2].category == "crypto"
    print(f"  pair1 open/close/min/max = "
          f"{r1.net_open}/{r1.net_close}/{r1.net_min}/{r1.net_max}")


def test_cutoff():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    assert cutoff(now, 14) == datetime(2026, 5, 18, tzinfo=UTC)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
