"""Holdings-relevant news for the daily briefing. §11.

Primary source is Alpha Vantage ``NEWS_SENTIMENT`` (free, ticker-tagged with a
sentiment score); supplemented by Finnhub company news. Items are de-duplicated
by URL/title and the most relevant/recent are kept. Everything here is
network-only and fail-soft: any source error degrades to fewer items rather
than crashing the briefing.

``dedupe`` and ``rank`` are pure and unit-testable offline; ``fetch_news`` does
the live wiring.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MAX_ITEMS = 12          # keep the brief readable; Haiku gets a focused set
PER_TICKER = 4          # cap per holding so one noisy ticker can't dominate


@dataclass
class NewsItem:
    ticker: str
    title: str
    summary: str
    url: str
    source: str
    sentiment: float | None = None     # -1..1 where available (Alpha Vantage)
    published: str | None = None       # ISO8601 where available
    tickers: list[str] = field(default_factory=list)


def _key(item: NewsItem) -> str:
    """Dedup key: prefer URL, fall back to normalized title."""
    if item.url:
        return item.url.split("?")[0].rstrip("/").lower()
    return " ".join(item.title.lower().split())


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Drop duplicates by URL/title, keeping the first (higher-priority) hit. Pure."""
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in items:
        k = _key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _recency(item: NewsItem) -> float:
    if not item.published:
        return 0.0
    try:
        ts = datetime.fromisoformat(item.published.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    except Exception:
        return 0.0


def rank(items: list[NewsItem], limit: int = MAX_ITEMS) -> list[NewsItem]:
    """Sort by recency then |sentiment| (strong signals first), cap to limit. Pure."""
    items = sorted(
        items,
        key=lambda it: (_recency(it), abs(it.sentiment or 0.0)),
        reverse=True,
    )
    return items[:limit]


def fetch_news(tickers: list[str]) -> list[NewsItem]:  # pragma: no cover - network
    """Best-effort news for the given holdings. Returns ranked, de-duped items.

    Order of preference: Alpha Vantage NEWS_SENTIMENT (ticker-tagged + scored),
    then Finnhub company news as a supplement. Always returns a list; on total
    source failure that list is empty and the briefing proceeds without news.
    """
    items: list[NewsItem] = []
    items.extend(_alpha_vantage_news(tickers))
    items.extend(_finnhub_news(tickers))
    ranked = rank(dedupe(items))
    log.info("news: %s raw -> %s after dedupe/rank", len(items), len(ranked))
    return ranked


def _alpha_vantage_news(tickers: list[str]) -> list[NewsItem]:  # pragma: no cover - network
    import requests

    key = os.environ.get("ALPHAVANTAGE_KEY")
    if not key or not tickers:
        return []
    out: list[NewsItem] = []
    # One call tagged with all tickers; Alpha Vantage returns per-article ticker
    # sentiment we can attribute back to each holding.
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "NEWS_SENTIMENT",
                "tickers": ",".join(tickers),
                "sort": "LATEST",
                "limit": 50,
                "apikey": key,
            },
            timeout=25,
        )
        feed = r.json().get("feed", []) or []
    except Exception as e:
        log.warning("Alpha Vantage news failed: %s", e)
        return []

    held = {t.upper() for t in tickers}
    for art in feed:
        ts = art.get("ticker_sentiment", []) or []
        # Attribute the article to each held ticker it mentions.
        matched = [s for s in ts if s.get("ticker", "").upper() in held]
        title = art.get("title", "").strip()
        url = art.get("url", "")
        summary = (art.get("summary", "") or "").strip()
        source = art.get("source", "Alpha Vantage")
        published = _av_time(art.get("time_published"))
        if not matched:
            continue
        kept = 0
        for s in matched:
            if kept >= PER_TICKER:
                break
            try:
                sent = float(s.get("ticker_sentiment_score"))
            except (TypeError, ValueError):
                sent = None
            out.append(NewsItem(
                ticker=s["ticker"].upper(), title=title, summary=summary,
                url=url, source=source, sentiment=sent, published=published,
                tickers=[m["ticker"].upper() for m in matched],
            ))
            kept += 1
    return out


def _av_time(raw: str | None) -> str | None:
    """Alpha Vantage stamps look like 20260625T133000 -> ISO8601."""
    if not raw or len(raw) < 15:
        return None
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


def _finnhub_news(tickers: list[str]) -> list[NewsItem]:  # pragma: no cover - network
    import requests

    key = os.environ.get("FINNHUB_KEY")
    if not key or not tickers:
        return []
    from datetime import date, timedelta

    today = date.today()
    frm = (today - timedelta(days=3)).isoformat()
    to = today.isoformat()
    out: list[NewsItem] = []
    for t in tickers:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": t, "from": frm, "to": to, "token": key},
                timeout=20,
            )
            arts = r.json() or []
        except Exception as e:
            log.warning("Finnhub news failed for %s: %s", t, e)
            continue
        for art in arts[:PER_TICKER]:
            published = None
            if art.get("datetime"):
                try:
                    published = datetime.fromtimestamp(
                        art["datetime"], tz=timezone.utc).isoformat()
                except Exception:
                    published = None
            out.append(NewsItem(
                ticker=t.upper(),
                title=(art.get("headline", "") or "").strip(),
                summary=(art.get("summary", "") or "").strip(),
                url=art.get("url", ""),
                source=art.get("source", "Finnhub"),
                sentiment=None,
                published=published,
                tickers=[t.upper()],
            ))
    return out
