"""Current prices + per-holding P&L. §11.

Prices via yfinance (free); fall back to Alpha Vantage GLOBAL_QUOTE when
yfinance returns nothing. `compute_pnl` is pure so it is unit-tested offline.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .portfolio import Holding

log = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    shares: float
    avg_cost: float
    price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pct: float | None


def compute_pnl(holdings: list[Holding], prices: dict[str, float]) -> tuple[list[Position], dict]:
    """Per-holding unrealized P&L + portfolio totals. Pure.

    P&L = (price - avg_cost) * shares. A missing price yields a Position with
    None metrics (never crashes the briefing).
    """
    positions: list[Position] = []
    total_value = 0.0
    total_cost = 0.0
    total_pnl = 0.0
    for h in holdings:
        price = prices.get(h.ticker)
        if price is None:
            positions.append(Position(h.ticker, h.shares, h.avg_cost, None, None, None, None))
            continue
        mv = price * h.shares
        cost = h.avg_cost * h.shares
        pnl = mv - cost
        pct = (pnl / cost) if cost else None
        positions.append(Position(h.ticker, h.shares, h.avg_cost, price, mv, pnl, pct))
        total_value += mv
        total_cost += cost
        total_pnl += pnl
    totals = {
        "market_value": total_value,
        "cost_basis": total_cost,
        "unrealized_pnl": total_pnl,
        "unrealized_pct": (total_pnl / total_cost) if total_cost else None,
    }
    return positions, totals


def fetch_prices(tickers: list[str]) -> dict[str, float]:  # pragma: no cover - network
    """Best-effort current prices. yfinance first, Alpha Vantage fallback."""
    out: dict[str, float] = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
        data = yf.download(tickers, period="1d", progress=False)
        closes = data["Close"]
        for t in tickers:
            try:
                val = closes[t].dropna().iloc[-1] if len(tickers) > 1 else closes.dropna().iloc[-1]
                out[t] = float(val)
            except Exception:
                pass
    except Exception as e:
        log.warning("yfinance failed: %s", e)

    missing = [t for t in tickers if t not in out]
    if missing:
        out.update(_alpha_vantage_quotes(missing))
    return out


def _alpha_vantage_quotes(tickers: list[str]) -> dict[str, float]:  # pragma: no cover - network
    import requests
    key = os.environ.get("ALPHAVANTAGE_KEY")
    if not key:
        return {}
    out: dict[str, float] = {}
    for t in tickers:
        try:
            r = requests.get("https://www.alphavantage.co/query", params={
                "function": "GLOBAL_QUOTE", "symbol": t, "apikey": key}, timeout=20)
            price = r.json().get("Global Quote", {}).get("05. price")
            if price is not None:
                out[t] = float(price)
        except Exception as e:
            log.warning("Alpha Vantage quote failed for %s: %s", t, e)
    return out
