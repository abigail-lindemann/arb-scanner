"""Embedding-based candidate matching. §5.1.

Embed each platform's title (+ first sentence of description) with
sentence-transformers all-MiniLM-L6-v2, compute cross-platform cosine
similarity, and emit candidate pairs above a threshold (H3-tunable 0.60),
keeping the top few Kalshi candidates per Polymarket market.

The real model is loaded lazily so importing this module is cheap and so
tests can inject a stub encoder (any callable: list[str] -> 2D float array).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
CANDIDATE_THRESHOLD = 0.60  # H3: tune after observing real match quality
TOP_K_PER_PM = 3

Encoder = Callable[[Sequence[str]], np.ndarray]

_model = None  # cached SentenceTransformer instance
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _default_encoder(texts: Sequence[str]) -> np.ndarray:
    """Lazily load all-MiniLM-L6-v2 and L2-normalize its embeddings."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # heavy import
        log.info("loading embedding model %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
    emb = _model.encode(list(texts), normalize_embeddings=True)
    return np.asarray(emb, dtype=np.float32)


def embed_text(title: str, description: str | None) -> str:
    """title + first sentence of description (§5.1)."""
    desc = (description or "").strip()
    first = _SENT_SPLIT.split(desc)[0] if desc else ""
    return f"{title}. {first}".strip() if first else title


@dataclass
class Candidate:
    pm_market_id: str
    kalshi_market_id: str
    pm_title: str
    kalshi_title: str
    pm_desc: str
    kalshi_desc: str
    pm_outcomes: list
    kalshi_outcomes: list
    embedding_sim: float


def candidate_pairs(
    df: pd.DataFrame,
    encoder: Encoder | None = None,
    threshold: float = CANDIDATE_THRESHOLD,
    top_k: int = TOP_K_PER_PM,
) -> list[Candidate]:
    """Cross-platform candidate pairs with cosine >= threshold.

    encoder defaults to the real MiniLM model; pass a stub in tests.
    Embeddings are assumed L2-normalized, so cosine == dot product.
    """
    encoder = encoder or _default_encoder
    pm = df[df["platform"] == "polymarket"].to_dict("records")
    ks = df[df["platform"] == "kalshi"].to_dict("records")
    if not pm or not ks:
        return []

    pm_emb = encoder([embed_text(r["title"], r["description"]) for r in pm])
    ks_emb = encoder([embed_text(r["title"], r["description"]) for r in ks])
    sims = pm_emb @ ks_emb.T  # (n_pm, n_ks)

    out: list[Candidate] = []
    for i, prow in enumerate(pm):
        row = sims[i]
        # indices of the top_k Kalshi markets, best first
        order = np.argsort(row)[::-1][:top_k]
        for j in order:
            s = float(row[j])
            if s < threshold:
                break  # sorted desc: nothing further qualifies
            krow = ks[j]
            out.append(Candidate(
                pm_market_id=prow["market_id"],
                kalshi_market_id=krow["market_id"],
                pm_title=prow["title"],
                kalshi_title=krow["title"],
                pm_desc=prow["description"],
                kalshi_desc=krow["description"],
                pm_outcomes=prow["outcomes"],
                kalshi_outcomes=krow["outcomes"],
                embedding_sim=s,
            ))
    log.info("candidate_pairs: %s candidates (threshold=%.2f)", len(out), threshold)
    return out
