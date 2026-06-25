"""Net-spread computation with inversion handling. See BUILD_GUIDE §7.2.

Two price roles:
  - prob_mid drives the scatter/agreement visual (clean midpoint).
  - executable bid/ask drives the actionable net spread in the tables
    (real arbitrage means crossing the spread on both legs).
"""
from __future__ import annotations

from dataclasses import dataclass

from .fees import kalshi_fee, pm_fee

THRESHOLD = 0.05            # 5% net-of-fees flag
RESOLUTION_WARN_BELOW = 0.8  # warn if resolution_match_score < this
POSITION_USD = 100.0       # standard illustration size


@dataclass
class SpreadResult:
    gross_spread: float
    net_spread: float
    est_edge_usd: float
    is_opportunity: bool
    resolution_warning: bool
    # the inversion-aligned probabilities actually compared
    pm_aligned: float
    kalshi_aligned: float


def _align_kalshi(kalshi_prob: float, inverted: bool) -> float:
    """If inverted, Polymarket-Yes corresponds to Kalshi-No."""
    return (1.0 - kalshi_prob) if inverted else kalshi_prob


def compute_spread(
    pm_prob: float,
    kalshi_prob: float,
    category: str,
    inverted: bool = False,
    resolution_match_score: float = 1.0,
    maker: bool = False,
) -> SpreadResult:
    """Compute gross/net spread for one aligned pair.

    Pass MID probabilities for the scatter-visual view, or the executable
    leg probabilities (pm_yes_ask on the buy leg, etc.) for the actionable
    table view. Fees are charged at each leg's own price.
    """
    k = _align_kalshi(kalshi_prob, inverted)
    gross = abs(pm_prob - k)
    net = gross - pm_fee(pm_prob, category, maker) - kalshi_fee(k, maker)
    net = max(net, 0.0)
    return SpreadResult(
        gross_spread=gross,
        net_spread=net,
        est_edge_usd=net * POSITION_USD,
        is_opportunity=net >= THRESHOLD,
        resolution_warning=resolution_match_score < RESOLUTION_WARN_BELOW,
        pm_aligned=pm_prob,
        kalshi_aligned=k,
    )
