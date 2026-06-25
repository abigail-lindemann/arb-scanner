"""Polymarket ingestion via the public Gamma + CLOB APIs (no auth). §4.1.

Gamma gives the market list + decimal outcome prices (0-1).
CLOB gives executable bid/ask + midpoint per token -- fetched ONLY for
matched markets (§4.1) to keep call volume low.

Fail-soft: a bad market or a single failed request is logged and skipped,
never fatal to the whole run (rule §0.5 / §0.5).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
_PAGE = 100
_TIMEOUT = 20

_session = requests.Session()
_session.headers.update({"User-Agent": "arb-scanner/1.0"})

_retry = retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    reraise=True,
)


@_retry
def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    r = _session.get(url, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_markets(top_n: int = 300) -> list[dict[str, Any]]:
    """Return up to ~top_n active Polymarket markets, most active first.

    Pages through Gamma ordered by 24h volume descending.
    """
    out: list[dict[str, Any]] = []
    offset = 0
    while len(out) < top_n:
        params = {
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": _PAGE,
            "offset": offset,
        }
        try:
            page = _get(f"{GAMMA_BASE}/markets", params)
        except requests.RequestException as e:
            log.warning("Polymarket page at offset %s failed: %s", offset, e)
            break
        if not page:
            break
        out.extend(page)
        offset += _PAGE
        if len(page) < _PAGE:
            break
    log.info("Polymarket: fetched %s markets", len(out))
    return out[:top_n]


def parse_outcome_prices(market: dict[str, Any]) -> list[tuple[str, float]]:
    """Parse Gamma's JSON-string outcomes/outcomePrices into (label, prob) pairs.

    Gamma encodes these as JSON strings, e.g. '["Yes","No"]' / '["0.62","0.38"]'.
    Returns [] on any malformed field rather than raising.
    """
    try:
        labels = market.get("outcomes")
        prices = market.get("outcomePrices")
        if isinstance(labels, str):
            labels = json.loads(labels)
        if isinstance(prices, str):
            prices = json.loads(prices)
        if not labels or not prices or len(labels) != len(prices):
            return []
        return [(str(lbl), float(p)) for lbl, p in zip(labels, prices)]
    except (ValueError, TypeError) as e:
        log.warning("PM parse drop %s: outcomes=%r prices=%r", market.get("id"), market.get("outcomes"), market.get("outcomePrices"))
        return []


@_retry
def _clob_price(token_id: str, side: str) -> float | None:
    data = _get(f"{CLOB_BASE}/price", {"token_id": token_id, "side": side})
    val = data.get("price")
    return float(val) if val is not None else None


@_retry
def _clob_midpoint(token_id: str) -> float | None:
    data = _get(f"{CLOB_BASE}/midpoint", {"token_id": token_id})
    val = data.get("mid")
    return float(val) if val is not None else None


def enrich_bidask(token_id: str) -> dict[str, float | None]:
    """Executable bid/ask/mid for one CLOB token (Yes side).

    Called only for matched markets. Fail-soft: returns Nones on error.
    """
    try:
        return {
            "prob_yes_bid": _clob_price(token_id, "buy"),
            "prob_yes_ask": _clob_price(token_id, "sell"),
            "prob_mid": _clob_midpoint(token_id),
        }
    except requests.RequestException as e:
        log.warning("CLOB enrich failed for token %s: %s", token_id, e)
        return {"prob_yes_bid": None, "prob_yes_ask": None, "prob_mid": None}


def yes_token_id(market: dict[str, Any]) -> str | None:
    """Best-effort extract of the 'Yes' CLOB token id for bid/ask enrichment."""
    ids = market.get("clobTokenIds")
    try:
        if isinstance(ids, str):
            ids = json.loads(ids)
        if ids:
            return str(ids[0])  # convention: index 0 == first/"Yes" outcome
    except (ValueError, TypeError):
        pass
    return None
