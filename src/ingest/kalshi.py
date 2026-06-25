"""Kalshi ingestion via the public market-data API (no auth). §4.2.

Only trading needs RSA keys; market data is open. Prices are cents 1-99.
Cursor pagination. Fail-soft per page.
"""
from __future__ import annotations

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

BASE = "https://external-api.kalshi.com/trade-api/v2"
BASE_FALLBACK = "https://trading-api.kalshi.com/trade-api/v2"
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
def _get(base: str, path: str, params: dict[str, Any]) -> Any:
    r = _session.get(f"{base}{path}", params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _get_with_fallback(path: str, params: dict[str, Any]) -> Any:
    try:
        return _get(BASE, path, params)
    except requests.RequestException as e:
        log.warning("Kalshi primary base failed (%s); trying fallback", e)
        return _get(BASE_FALLBACK, path, params)


def fetch_markets(top_n: int = 300) -> list[dict[str, Any]]:
    """Return open Kalshi markets, then keep the top_n by 24h volume.

    Kalshi's feed isn't volume-ordered, so we page broadly then sort.
    """
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    # cap pages so a huge feed can't run away; ~10 pages * 100 = 1000 candidates
    for _ in range(10):
        params: dict[str, Any] = {"status": "open", "limit": _PAGE}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _get_with_fallback("/markets", params)
        except requests.RequestException as e:
            log.warning("Kalshi page failed: %s", e)
            break
        markets = data.get("markets", [])
        out.extend(markets)
        cursor = data.get("cursor")
        if not cursor or not markets:
            break
    out.sort(key=lambda m: float(m.get("volume_24h_fp") or m.get("volume_24h") or 0), reverse=True)
    log.info("Kalshi: fetched %s markets, keeping top %s", len(out), top_n)
    return out[:top_n]
