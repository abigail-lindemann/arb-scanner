"""Compose the daily briefing email with one Claude Haiku call. §11.

Input is the assembled portfolio P&L, ranked news, and the scanner's current
top opportunities. Output is a complete HTML email whose first block is a
"what matters" bullet summary, followed by short, plainly-labelled sections.

Tone is strictly **informational** — it describes positions, moves, news, and
computed signals; it never recommends buying or selling (§0, "not financial
advice").

Design notes:
- ``build_facts`` and ``render_html`` are pure and unit-testable offline.
- ``summarize`` makes the live Haiku call with the stable instruction block
  marked for **prompt caching** (cost control, §13), and falls back to a fully
  templated HTML brief if the API errors — the email always sends.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1100

# Stable across every run -> eligible for prompt caching. Keep wording fixed.
SYSTEM = (
    "You write a concise twice-daily market briefing for one private investor. "
    "You are informational only: describe positions, moves, news, and computed "
    "signals. Never advise buying or selling and never predict prices. "
    "Open with a section headed 'What matters' containing 3-5 short bullets of "
    "the most important things right now. Then short sections: 'Your holdings' "
    "(overnight moves and P&L), 'News on your names', and 'Scanner signals' "
    "(the supplied cross-platform arbitrage flags, already net of fees). "
    "Be specific and brief. Output clean semantic HTML using only <h2>, <h3>, "
    "<ul>, <li>, <p>, <strong>, and <span> tags — no <html>, <head>, <style>, "
    "or markdown fences. Do not invent numbers; use only the supplied facts."
)


@dataclass
class BriefInput:
    positions: list[Any]      # prices.Position
    totals: dict
    news: list[Any]           # news.NewsItem
    top_signals: list[dict]


def _f(x: float | None, pct: bool = False, usd: bool = False) -> str:
    if x is None:
        return "n/a"
    if pct:
        return f"{x * 100:+.1f}%"
    if usd:
        return f"${x:,.2f}"
    return f"{x:,.2f}"


def build_facts(brief: BriefInput) -> dict:
    """Flatten the inputs into the compact JSON fact sheet Haiku receives. Pure."""
    positions = []
    for p in brief.positions:
        positions.append({
            "ticker": p.ticker,
            "shares": p.shares,
            "avg_cost": p.avg_cost,
            "price": p.price,
            "market_value": p.market_value,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_pct": p.unrealized_pct,
        })
    news = []
    for n in brief.news:
        news.append({
            "ticker": n.ticker,
            "title": n.title,
            "source": n.source,
            "sentiment": n.sentiment,
            "url": n.url,
        })
    signals = []
    for s in brief.top_signals:
        signals.append({
            "event": s.get("event") or s.get("title") or s.get("pm_title"),
            "category": s.get("category"),
            "net_spread": s.get("net_spread"),
            "confidence": s.get("confidence"),
            "pm_prob": s.get("pm_prob_mid") or s.get("pm_prob"),
            "kalshi_prob": s.get("kalshi_prob_mid") or s.get("kalshi_prob"),
            "resolution_warning": s.get("resolution_warning"),
        })
    return {"portfolio_totals": brief.totals, "positions": positions,
            "news": news, "signals": signals}


def render_html(narrative_html: str) -> str:
    """Wrap Haiku's section HTML in the email shell (inline styles for clients). Pure."""
    return f"""\
<div style="font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
            background:#0a0e0f;color:#d7e0dc;padding:24px;max-width:680px;margin:auto;
            border:1px solid #1b2429;border-radius:10px">
  <div style="color:#00ff9c;font-weight:600;letter-spacing:1px;font-size:13px;
              border-bottom:1px solid #1b2429;padding-bottom:10px;margin-bottom:16px">
    MARKET BRIEFING
  </div>
  <div style="font-size:14px;line-height:1.55">{narrative_html}</div>
  <p style="color:#7c8c86;font-size:11px;margin-top:22px;border-top:1px solid #1b2429;padding-top:10px">
    Informational only — computed signals are net of conservative taker fees and
    are not financial advice. Acting on any signal is a separate manual decision.
  </p>
</div>"""


def render_fallback_html(brief: BriefInput) -> str:
    """Deterministic HTML brief used when the Haiku call fails. Pure, no network."""
    t = brief.totals
    parts: list[str] = ["<h2>What matters</h2><ul>"]
    parts.append(
        f"<li>Portfolio value <strong>{_f(t.get('market_value'), usd=True)}</strong>, "
        f"unrealized <strong>{_f(t.get('unrealized_pnl'), usd=True)}</strong> "
        f"({_f(t.get('unrealized_pct'), pct=True)}).</li>")
    if brief.top_signals:
        best = brief.top_signals[0]
        parts.append(
            f"<li>Top scanner signal: {best.get('event') or 'a matched pair'} at "
            f"net spread <strong>{_f(best.get('net_spread'), pct=True)}</strong>.</li>")
    if brief.news:
        parts.append(f"<li>{len(brief.news)} relevant news items on your names.</li>")
    parts.append("</ul>")

    parts.append("<h2>Your holdings</h2><ul>")
    for p in brief.positions:
        parts.append(
            f"<li><strong>{p.ticker}</strong> — {_f(p.price, usd=True)} "
            f"({_f(p.unrealized_pct, pct=True)}), "
            f"P&amp;L {_f(p.unrealized_pnl, usd=True)}</li>")
    if not brief.positions:
        parts.append("<li>No holdings recorded — running on market and news only.</li>")
    parts.append("</ul>")

    if brief.news:
        parts.append("<h2>News on your names</h2><ul>")
        for n in brief.news[:8]:
            link = f'<a href="{n.url}" style="color:#00ff9c">{n.title}</a>' if n.url else n.title
            parts.append(f"<li><strong>{n.ticker}</strong> — {link} <span style='color:#7c8c86'>({n.source})</span></li>")
        parts.append("</ul>")

    if brief.top_signals:
        parts.append("<h2>Scanner signals</h2><ul>")
        for s in brief.top_signals[:6]:
            warn = " ⚠ resolution mismatch" if s.get("resolution_warning") else ""
            parts.append(
                f"<li>{s.get('event') or 'matched pair'} — net "
                f"<strong>{_f(s.get('net_spread'), pct=True)}</strong>{warn}</li>")
        parts.append("</ul>")

    return render_html("".join(parts))


def summarize(client, brief: BriefInput) -> str:  # pragma: no cover - network
    """One Haiku call -> full HTML email. Falls back to a templated brief on error."""
    facts = build_facts(brief)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},   # §13 prompt caching
            }],
            messages=[{
                "role": "user",
                "content": (
                    "Write today's briefing from these facts (JSON). Use only "
                    "these numbers.\n\n" + json.dumps(facts, default=str)
                ),
            }],
        )
        narrative = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        if not narrative:
            raise ValueError("empty narrative from model")
        return render_html(narrative)
    except Exception as e:
        log.warning("Haiku summarize failed (%s) — using templated fallback", e)
        return render_fallback_html(brief)
