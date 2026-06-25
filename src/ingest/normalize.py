"""Normalize Polymarket + Kalshi into one unified DataFrame. §4.3.

Unified row schema (one row per market; primary/Yes outcome drives prob_mid,
full outcome list preserved in `outcomes` for the alignment agent):

  platform, market_id, event_id, title, description, category,
  outcomes, prob_mid, prob_yes_bid, prob_yes_ask,
  volume_24h, liquidity, end_date, status

Polymarket prices are decimals 0-1 already. Kalshi prices are cents 1-99
and are divided by 100 here.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from ..arb.fees import PM_CATEGORY_MAX
from . import polymarket as pm

log = logging.getLogger(__name__)

UNIFIED_COLUMNS = [
    "platform", "market_id", "event_id", "title", "description", "category",
    "outcomes", "prob_mid", "prob_yes_bid", "prob_yes_ask",
    "volume_24h", "liquidity", "end_date", "status",
]

# Fee-table buckets the category MUST resolve to (keys of PM_CATEGORY_MAX).
_VALID_BUCKETS = set(PM_CATEGORY_MAX)

# ---------------------------------------------------------------------------
# H2: raw-category -> fee-bucket mapping. THIS IS A HUMAN-VERIFIED TABLE.
# Platforms name categories differently and change them over time. A wrong
# mapping silently applies the wrong fee and corrupts every net-spread for
# that market. Unmapped categories fall back to the conservative (highest)
# fee in fees.py and are logged here for review. Verify against a real
# sample of each platform's live category/tag values before trusting fees.
# ---------------------------------------------------------------------------
PM_CATEGORY_MAP: dict[str, str] = {
    "crypto": "crypto", "cryptocurrency": "crypto", "bitcoin": "crypto", "ethereum": "crypto",
    "economics": "economics", "economy": "economics", "macro": "economics",
    "culture": "culture", "pop culture": "culture", "entertainment": "culture", "music": "culture",
    "weather": "weather", "climate": "weather",
    "politics": "politics", "elections": "politics", "us-current-affairs": "politics",
    "finance": "finance", "business": "finance", "stocks": "finance",
    "tech": "tech", "technology": "tech", "ai": "tech",
    "mentions": "mentions",
    "sports": "sports", "nfl": "sports", "nba": "sports", "soccer": "sports", "mlb": "sports",
    "geopolitics": "geopolitics",
    "world": "world",
}

KALSHI_CATEGORY_MAP: dict[str, str] = {
    "crypto": "crypto",
    "economics": "economics", "economy": "economics",
    "climate and weather": "weather", "weather": "weather", "climate": "weather",
    "politics": "politics", "elections": "politics",
    "financials": "finance", "companies": "finance", "financial": "finance",
    "science and technology": "tech", "technology": "tech", "science": "tech",
    "entertainment": "culture", "culture": "culture",
    "sports": "sports",
    "world": "world",
}


def map_category(raw: str | None, mapping: dict[str, str]) -> str:
    """Resolve a raw platform category to a fee bucket.

    Returns a valid bucket when known. Otherwise returns the raw string
    (lowercased) UNCHANGED so fees.py treats it as unmapped and applies the
    conservative cap; the caller logs it for human review (H2).
    """
    if not raw:
        return "other"  # missing category -> conservative 'other' bucket
    key = raw.strip().lower()
    bucket = mapping.get(key)
    if bucket is None:
        log.warning("UNMAPPED category %r -> conservative fee fallback (H2 review)", raw)
        return key
    return bucket


def _pm_category_raw(market: dict[str, Any]) -> str | None:
    # Gamma exposes category and/or tags; prefer explicit category, then first tag.
    cat = market.get("category")
    if cat:
        return cat
    tags = market.get("tags") or market.get("events")
    if isinstance(tags, list) and tags:
        first = tags[0]
        if isinstance(first, dict):
            return first.get("label") or first.get("slug") or first.get("title")
        return str(first)
    return None


def normalize_pm_market(market: dict[str, Any]) -> dict[str, Any] | None:
    """One unified row from a Polymarket Gamma market. None if unusable."""
    pairs = pm.parse_outcome_prices(market)
    if not pairs:
        return None
    prob_mid = pairs[0][1]  # primary/"Yes" outcome
    title = market.get("question") or market.get("title")
    if not title or prob_mid is None:
        return None
    return {
        "platform": "polymarket",
        "market_id": market.get("conditionId") or market.get("slug") or market.get("id"),
        "event_id": market.get("eventSlug") or market.get("groupItemTitle"),
        "title": title,
        "description": market.get("description") or "",
        "category": map_category(_pm_category_raw(market), PM_CATEGORY_MAP),
        "outcomes": [{"label": l, "prob": p} for l, p in pairs],
        "prob_mid": prob_mid,
        # PM bid/ask comes from CLOB enrichment later (matched markets only).
        "prob_yes_bid": None,
        "prob_yes_ask": None,
        "volume_24h": _to_float(market.get("volume24hr")),
        "liquidity": _to_float(market.get("liquidity")),
        "end_date": market.get("endDate"),
        "status": "open",
    }


def normalize_kalshi_market(market: dict[str, Any]) -> dict[str, Any] | None:
    """One unified row from a Kalshi market. None if unusable."""
    title = market.get("title")
    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    if title is None or yes_bid is None or yes_ask is None:
        return None
    prob_bid = yes_bid / 100.0
    prob_ask = yes_ask / 100.0
    prob_mid = (yes_bid + yes_ask) / 2.0 / 100.0
    raw_cat = market.get("category") or market.get("series_ticker")
    return {
        "platform": "kalshi",
        "market_id": market.get("ticker"),
        "event_id": market.get("event_ticker"),
        "title": (title + " " + (market.get("subtitle") or "")).strip(),
        "description": market.get("rules_primary") or market.get("subtitle") or "",
        "category": map_category(raw_cat, KALSHI_CATEGORY_MAP),
        "outcomes": [{"label": "Yes", "prob": prob_mid},
                     {"label": "No", "prob": round(1.0 - prob_mid, 6)}],
        "prob_mid": prob_mid,
        "prob_yes_bid": prob_bid,
        "prob_yes_ask": prob_ask,
        "volume_24h": _to_float(market.get("volume_24h")),
        # Kalshi has no 'liquidity' field; open interest is the closest proxy.
        "liquidity": _to_float(market.get("open_interest")),
        "end_date": market.get("close_time"),
        "status": market.get("status") or "open",
    }


def _to_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_unified(
    pm_markets: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
) -> pd.DataFrame:
    """Normalize both feeds into one DataFrame, skipping unusable rows."""
    rows: list[dict[str, Any]] = []
    for m in pm_markets:
        try:
            r = normalize_pm_market(m)
            if r:
                rows.append(r)
        except Exception as e:  # fail-soft per item
            log.warning("normalize PM market failed: %s", e)
    for m in kalshi_markets:
        try:
            r = normalize_kalshi_market(m)
            if r:
                rows.append(r)
        except Exception as e:
            log.warning("normalize Kalshi market failed: %s", e)
    df = pd.DataFrame(rows, columns=UNIFIED_COLUMNS)
    log.info("Unified DataFrame: %s rows (pm+kalshi)", len(df))
    return df


def unmapped_categories(df: pd.DataFrame) -> set[str]:
    """Categories that fell through to the conservative fallback -> H2 review."""
    return {c for c in df["category"].unique() if c not in _VALID_BUCKETS}
