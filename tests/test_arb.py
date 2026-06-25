"""Offline tests for the fee + net-spread math (BUILD_GUIDE §7.1/§7.2).
Run: python -m pytest tests/test_arb.py -q   (or just `python tests/test_arb.py`)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.arb.fees import kalshi_fee, pm_fee
from src.arb.spread import compute_spread


def test_fees_peak_at_half():
    # parabola peaks at p=0.5 and is zero at the extremes
    assert pm_fee(0.5, "crypto") > pm_fee(0.2, "crypto") > pm_fee(0.02, "crypto")
    assert abs(pm_fee(0.5, "crypto") - 0.0180) < 1e-9
    assert abs(kalshi_fee(0.5) - 0.0175) < 1e-9
    assert pm_fee(0.0, "crypto") == 0.0 and pm_fee(1.0, "crypto") == 0.0


def test_unmapped_category_uses_conservative_cap():
    assert pm_fee(0.5, "made_up_category") == max(
        pm_fee(0.5, c) for c in ["crypto", "politics", "sports"]
    )


def test_acceptance_crypto_near_half_gross_6pct_drops_below_threshold():
    # §7.2 acceptance: high-fee crypto market near 50c with ~6% gross gap
    # should net out to a much smaller, sub-threshold spread.
    r = compute_spread(pm_prob=0.53, kalshi_prob=0.47, category="crypto")
    assert abs(r.gross_spread - 0.06) < 1e-9
    # fees: pm crypto @0.53 (~0.0179) + kalshi @0.47 (~0.01747) ~= 0.0354
    assert r.net_spread < r.gross_spread
    assert r.net_spread < 0.05 and not r.is_opportunity
    print(f"  crypto 53c/47c -> gross={r.gross_spread:.4f} "
          f"net={r.net_spread:.4f} edge=${r.est_edge_usd:.2f} opp={r.is_opportunity}")


def test_low_fee_wide_gap_survives_as_opportunity():
    # geopolitics is fee-free on PM; a wide gap should remain an opportunity
    r = compute_spread(pm_prob=0.70, kalshi_prob=0.58, category="geopolitics")
    assert r.is_opportunity and r.net_spread >= 0.05


def test_inversion_alignment():
    # PM-Yes 0.62 vs Kalshi-No, where Kalshi-Yes=0.40 -> aligned 0.60
    r = compute_spread(pm_prob=0.62, kalshi_prob=0.40, category="politics", inverted=True)
    assert abs(r.kalshi_aligned - 0.60) < 1e-9
    assert abs(r.gross_spread - 0.02) < 1e-9


def test_resolution_warning_flag():
    r = compute_spread(0.6, 0.5, "politics", resolution_match_score=0.5)
    assert r.resolution_warning is True
    r2 = compute_spread(0.6, 0.5, "politics", resolution_match_score=0.9)
    assert r2.resolution_warning is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
