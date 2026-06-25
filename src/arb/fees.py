"""Fee models for both platforms.

IMPORTANT (HUMAN CHECK H5): these closed-form curves are APPROXIMATIONS.
Both platforms' fees peak near p=0.5 and taper toward the extremes. The
constants must be validated against each platform's live fee calculator
before the net-spread signal is trusted. Treat outputs as strong estimates,
not guarantees.

Default assumption: TAKER fees on both legs (market orders, worst realistic
case). Makers pay zero on both platforms; pass maker=True to model that.
"""
from __future__ import annotations

# Polymarket: category -> max fee fraction of $1 notional at p=0.5 (V2, 2026).
PM_CATEGORY_MAX: dict[str, float] = {
    "crypto": 0.0180,
    "economics": 0.0125, "culture": 0.0125, "weather": 0.0125, "other": 0.0125,
    "politics": 0.0100, "finance": 0.0100, "tech": 0.0100, "mentions": 0.0100,
    "sports": 0.0075,
    "geopolitics": 0.0, "world": 0.0,
}

# Default for an unmapped category: the most conservative (highest) bucket,
# so a mis-mapped market overstates fees rather than overstating edge (H2).
_UNMAPPED_PM_CAP = max(PM_CATEGORY_MAX.values())


def pm_fee(prob: float, category: str, maker: bool = False) -> float:
    """Polymarket taker fee as a fraction of $1 notional at the given prob.

    Symmetric parabola peaking at the category cap at p=0.5.
    """
    if maker:
        return 0.0
    cap = PM_CATEGORY_MAX.get(category, _UNMAPPED_PM_CAP)
    return cap * (4 * prob * (1 - prob))


def kalshi_fee(prob: float, maker: bool = False) -> float:
    """Kalshi taker fee per contract, peaking ~0.0175 at p=0.5. Makers free."""
    if maker:
        return 0.0
    return 0.07 * prob * (1 - prob)


def is_unmapped_pm_category(category: str) -> bool:
    """True if this Polymarket category fell back to the conservative cap.
    Such rows should be logged for human review (H2)."""
    return category not in PM_CATEGORY_MAX
