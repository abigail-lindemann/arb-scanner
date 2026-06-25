"""Briefing entrypoint, run by GitHub Actions twice daily. §11 / §10.

DST handling (H6): the cron fires in UTC, which drifts against local time
across DST boundaries. The workflow schedules slightly early; this runner
computes the current time in America/Chicago and only sends when within the
intended window (08:00 / 20:00 +/- tolerance), otherwise exits quietly.

`within_window` is pure and unit-tested; `main` does the live wiring.
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TZ = ZoneInfo("America/Chicago")
SEND_HOURS = (8, 20)        # 08:00 and 20:00 local
TOLERANCE_MIN = 35          # cron fires early; accept a window after each target


def within_window(now_local: datetime, hours=SEND_HOURS, tol_min: int = TOLERANCE_MIN) -> bool:
    """True if now_local is within tol_min AFTER any target hour. Pure.

    We only accept times at/after the target (never before) so an early cron
    fire waits for the boundary rather than sending in the prior window.
    """
    for h in hours:
        target = now_local.replace(hour=h, minute=0, second=0, microsecond=0)
        delta_min = (now_local - target).total_seconds() / 60.0
        if 0 <= delta_min <= tol_min:
            return True
    return False


def main() -> None:  # pragma: no cover - live wiring, exercised in Actions
    import json
    import os
    from pathlib import Path

    import anthropic

    from .briefing.email_send import send_email
    from .briefing.news import fetch_news
    from .briefing.portfolio import load_portfolio
    from .briefing.prices import compute_pnl, fetch_prices
    from .briefing.summarize import BriefInput, summarize
    from .storage import db

    logging.basicConfig(level=logging.INFO)
    now_local = datetime.now(TZ)
    if not within_window(now_local):
        log.info("outside send window (%s local) — exiting without sending", now_local.isoformat())
        return

    conn = db.connect()
    holdings = load_portfolio(conn)
    conn.close()

    tickers = [h.ticker for h in holdings]
    prices = fetch_prices(tickers)
    positions, totals = compute_pnl(holdings, prices)
    news = fetch_news(tickers)

    # top signals from the scanner's latest data.json (no recompute)
    top_signals: list[dict] = []
    data_path = Path("docs/data.json")
    if data_path.exists():
        events = json.loads(data_path.read_text()).get("events", [])
        top_signals = sorted(events, key=lambda e: e.get("net_spread", 0), reverse=True)[:8]

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    html = summarize(client, BriefInput(positions, totals, news, top_signals))

    label = "Morning" if now_local.hour < 12 else "Evening"
    send_email(f"{label} market briefing — {now_local:%a %b %d}", html)
    log.info("briefing sent (%s local)", now_local.isoformat())


if __name__ == "__main__":  # pragma: no cover
    main()
