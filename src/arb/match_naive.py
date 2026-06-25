"""Naive title-equality matching -- a SANITY BRIDGE, not the real matcher.

Build-guide step 3: before the embeddings + LLM alignment layer (Phase 2)
exists, match Polymarket x Kalshi rows by normalized exact-title equality
and run them through the real fee/spread math. This proves the
ingestion -> match -> spread pipeline holds together end to end.

It will match almost nothing on real data (titles rarely match verbatim) --
that is expected and is exactly why Phase 2 (semantic matching) exists.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd

from .spread import SpreadResult, compute_spread

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for exact comparison."""
    t = _PUNCT.sub(" ", title.lower())
    return _WS.sub(" ", t).strip()


@dataclass
class NaivePair:
    pm_market_id: str
    kalshi_market_id: str
    title: str
    category: str
    pm_mid: float
    kalshi_mid: float
    spread: SpreadResult


def naive_match(df: pd.DataFrame) -> list[NaivePair]:
    """Pair PM and Kalshi rows whose normalized titles are identical.

    Uses prob_mid on both legs (agreement view). The PM market's category
    drives the Polymarket-leg fee.
    """
    pm = df[df["platform"] == "polymarket"].copy()
    ks = df[df["platform"] == "kalshi"].copy()
    pm["norm"] = pm["title"].map(normalize_title)
    ks["norm"] = ks["title"].map(normalize_title)

    ks_by_norm: dict[str, list] = {}
    for row in ks.to_dict("records"):
        ks_by_norm.setdefault(row["norm"], []).append(row)

    pairs: list[NaivePair] = []
    for prow in pm.to_dict("records"):
        for krow in ks_by_norm.get(prow["norm"], []):
            res = compute_spread(
                pm_prob=prow["prob_mid"],
                kalshi_prob=krow["prob_mid"],
                category=prow["category"],
            )
            pairs.append(NaivePair(
                pm_market_id=prow["market_id"],
                kalshi_market_id=krow["market_id"],
                title=prow["title"],
                category=prow["category"],
                pm_mid=prow["prob_mid"],
                kalshi_mid=krow["prob_mid"],
                spread=res,
            ))
    log.info("naive_match: %s exact-title pairs", len(pairs))
    return pairs
