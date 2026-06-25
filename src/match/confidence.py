"""Composite match confidence + high-confidence gating. §5.3.

    confidence = 0.5*embedding_sim + 0.3*resolution_match_score
               + 0.2*(1 if same_event else 0)

Keep only same_event pairs with confidence >= 0.80 for the high-confidence
views; everything else with same_event==true is routed to the "review"
surface. Binary pairs dominate the high-confidence set; multi-outcome pairs
are supported but expected to land lower (§5.3).

This heuristic is the Phase-A scorer. Phase B (ml/classifier.py) replaces or
augments it with a trained model's probability once labels exist.
"""
from __future__ import annotations

from dataclasses import dataclass

from .align_agent import Alignment
from .embed import Candidate

HIGH_CONFIDENCE = 0.80  # H3: tune after observing real precision/recall

_W_EMBED = 0.5
_W_RESOLUTION = 0.3
_W_SAME_EVENT = 0.2


def composite_confidence(embedding_sim: float, alignment: Alignment) -> float:
    return (
        _W_EMBED * embedding_sim
        + _W_RESOLUTION * alignment.resolution_match_score
        + _W_SAME_EVENT * (1.0 if alignment.same_event else 0.0)
    )


@dataclass
class ScoredPair:
    candidate: Candidate
    alignment: Alignment
    confidence: float

    @property
    def is_high_confidence(self) -> bool:
        return self.alignment.same_event and self.confidence >= HIGH_CONFIDENCE

    @property
    def needs_review(self) -> bool:
        """same_event but below the high-confidence bar -> review surface."""
        return self.alignment.same_event and not self.is_high_confidence


def score_pair(candidate: Candidate, alignment: Alignment) -> ScoredPair:
    return ScoredPair(
        candidate=candidate,
        alignment=alignment,
        confidence=composite_confidence(candidate.embedding_sim, alignment),
    )


def partition(scored: list[ScoredPair]) -> dict[str, list[ScoredPair]]:
    """Split scored pairs into high-confidence / review / rejected buckets."""
    high, review, rejected = [], [], []
    for sp in scored:
        if not sp.alignment.same_event:
            rejected.append(sp)
        elif sp.is_high_confidence:
            high.append(sp)
        else:
            review.append(sp)
    return {"high_confidence": high, "review": review, "rejected": rejected}
