"""End-to-end sanity test for the naive-match bridge (build-guide step 3).

Runs the full offline pipeline on mock payloads:
  raw dicts -> normalize.build_unified -> match_naive.naive_match -> spread
No network. Proves ingestion -> match -> spread math is wired correctly.
Run: python tests/test_pipeline_naive.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.normalize import build_unified
from src.arb.match_naive import naive_match, normalize_title

# A genuinely matchable pair: same event, titles normalize identically.
# Plus a non-matching pair to confirm we don't over-match.
SAMPLE_PM = [
    {  # matches the Kalshi BTC market after title normalization
        "conditionId": "0xbtc", "question": "Bitcoin above $100,000",
        "category": "Crypto",
        "outcomes": '["Yes","No"]', "outcomePrices": '["0.66","0.34"]',
        "volume24hr": "120000",
    },
    {  # no Kalshi counterpart
        "conditionId": "0xfed", "question": "Fed rate cut in July?",
        "category": "Economy",
        "outcomes": '["Yes","No"]', "outcomePrices": '["0.30","0.70"]',
    },
]

SAMPLE_KALSHI = [
    {
        "ticker": "BTC-100K", "event_ticker": "BTC",
        "title": "Bitcoin above $100,000", "subtitle": "",
        "yes_bid": 58, "yes_ask": 62, "volume_24h": 9000,
        "open_interest": 30000, "status": "open", "category": "Crypto",
    },
]


def test_title_normalization():
    assert normalize_title("Bitcoin above $100,000!") == "bitcoin above 100 000"
    assert normalize_title("Fed Rate Cut?") == "fed rate cut"


def test_pipeline_end_to_end():
    df = build_unified(SAMPLE_PM, SAMPLE_KALSHI)
    pairs = naive_match(df)
    # exactly one exact-title pair (the BTC market); the Fed market is unpaired
    assert len(pairs) == 1
    p = pairs[0]
    assert p.pm_market_id == "0xbtc" and p.kalshi_market_id == "BTC-100K"
    assert p.category == "crypto"
    # pm_mid 0.66 vs kalshi_mid (58+62)/2/100 = 0.60 -> gross 0.06
    assert abs(p.pm_mid - 0.66) < 1e-9
    assert abs(p.kalshi_mid - 0.60) < 1e-9
    assert abs(p.spread.gross_spread - 0.06) < 1e-9
    # crypto fees near 0.6 eat most of it -> net below the 5% flag
    assert p.spread.net_spread < p.spread.gross_spread
    print(f"  matched '{p.title}' [{p.category}] "
          f"pm={p.pm_mid:.2f} kalshi={p.kalshi_mid:.2f} "
          f"gross={p.spread.gross_spread:.4f} net={p.spread.net_spread:.4f} "
          f"opp={p.spread.is_opportunity}")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
