"""Offline tests for normalization + category mapping (BUILD_GUIDE §4.3, H2).

No network: feeds realistic sample raw payloads through the transforms and
checks the unified-DataFrame acceptance criteria.
Run: python tests/test_normalize.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.normalize import (
    build_unified,
    map_category,
    normalize_kalshi_market,
    normalize_pm_market,
    unmapped_categories,
    PM_CATEGORY_MAP,
    KALSHI_CATEGORY_MAP,
)

# --- sample raw payloads shaped like the real APIs ------------------------
SAMPLE_PM = [
    {
        "id": "0x01", "conditionId": "0xcond1", "slug": "btc-100k",
        "question": "Will BTC close above $100k in 2026?",
        "description": "Resolves YES if ...", "category": "Crypto",
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0.62", "0.38"]',
        "volume24hr": "120000", "liquidity": "45000",
        "endDate": "2026-12-31T00:00:00Z", "clobTokenIds": '["tok_yes","tok_no"]',
    },
    {
        "id": "0x02", "conditionId": "0xcond2", "slug": "fed-cut",
        "question": "Fed rate cut in July?", "description": "",
        "category": "Economy",
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0.30", "0.70"]',
        "volume24hr": "80000", "liquidity": "22000",
        "endDate": "2026-07-31T00:00:00Z",
    },
    {  # unusable: malformed prices -> must be skipped
        "id": "0x03", "conditionId": "0xcond3", "question": "Broken market",
        "outcomes": "not-json", "outcomePrices": "also-bad", "category": "Sports",
    },
    {  # weird category -> unmapped fallback, still a valid row
        "id": "0x04", "conditionId": "0xcond4", "question": "Mystery event?",
        "outcomes": '["Yes","No"]', "outcomePrices": '["0.5","0.5"]',
        "category": "Quantum Llamas", "volume24hr": "5",
    },
]

SAMPLE_KALSHI = [
    {
        "ticker": "BTC-26-100K", "event_ticker": "BTC-26",
        "title": "Bitcoin above $100,000", "subtitle": "at 2026 year end",
        "yes_bid": 60, "yes_ask": 64, "no_bid": 36, "no_ask": 40,
        "last_price": 62, "volume": 50000, "volume_24h": 9000,
        "open_interest": 30000, "status": "open",
        "close_time": "2026-12-31T00:00:00Z", "category": "Crypto",
    },
    {  # unusable: missing yes_bid -> skipped
        "ticker": "BAD-1", "title": "No prices here", "category": "Economics",
    },
]


def test_pm_normalization():
    r = normalize_pm_market(SAMPLE_PM[0])
    assert r["platform"] == "polymarket"
    assert r["category"] == "crypto"
    assert abs(r["prob_mid"] - 0.62) < 1e-9
    assert r["market_id"] == "0xcond1"
    assert r["outcomes"][0] == {"label": "Yes", "prob": 0.62}
    assert normalize_pm_market(SAMPLE_PM[2]) is None  # malformed -> skipped


def test_kalshi_normalization():
    r = normalize_kalshi_market(SAMPLE_KALSHI[0])
    assert r["platform"] == "kalshi"
    assert r["category"] == "crypto"
    # mid = (60+64)/2/100 = 0.62 ; bid/ask divided by 100
    assert abs(r["prob_mid"] - 0.62) < 1e-9
    assert abs(r["prob_yes_bid"] - 0.60) < 1e-9
    assert abs(r["prob_yes_ask"] - 0.64) < 1e-9
    assert normalize_kalshi_market(SAMPLE_KALSHI[1]) is None  # missing prices


def test_category_mapping_h2():
    assert map_category("Economy", PM_CATEGORY_MAP) == "economics"
    assert map_category("Climate and Weather", KALSHI_CATEGORY_MAP) == "weather"
    assert map_category(None, PM_CATEGORY_MAP) == "other"
    # unknown -> returns lowercased raw (fees.py then applies conservative cap)
    assert map_category("Quantum Llamas", PM_CATEGORY_MAP) == "quantum llamas"


def test_build_unified_acceptance():
    df = build_unified(SAMPLE_PM, SAMPLE_KALSHI)
    # 3 PM usable (skip the malformed one) + 1 Kalshi usable = 4 rows
    assert len(df) == 4
    # acceptance §4.3: no nulls in title / prob_mid / category
    for col in ("title", "prob_mid", "category"):
        assert df[col].notna().all(), f"nulls found in {col}"
    # H2: the bogus category is surfaced for review, not silently accepted
    assert "quantum llamas" in unmapped_categories(df)
    print(f"  unified rows={len(df)}  unmapped(H2)={unmapped_categories(df)}")
    print(df[["platform", "title", "category", "prob_mid"]].to_string(index=False))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
