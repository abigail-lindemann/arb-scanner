# Prediction Market Arbitrage Scanner + Daily Market Briefing

Two self-running tools on free, serverless infrastructure:

1. **Arb scanner** — a few times a day, pulls the most active markets from
   Polymarket and Kalshi, figures out which markets describe the *same*
   real-world event, and flags where the platforms disagree by more than 5%
   **after fees**. Surfaced as an agreement scatter plot + ranked tables on a
   static dashboard, with a signal **track-record** page that scores past
   flags honestly.
2. **Daily briefing agent** — twice a day, emails a written brief: portfolio
   P&L, news touching your holdings, and the scanner's best signals.

**Informational only. Never places trades, never gives buy/sell advice.**

## Architecture

GitHub Pages can't run code, so the system splits in two:

- **GitHub Actions = the engine.** Scheduled Python does all fetching, ML,
  DB writes, and email. Secrets live here only.
- **GitHub Pages = the window.** Serves `docs/`; the browser only reads
  `data.json` / `track-record.json`. No secrets, no API calls client-side.
- **Neon Postgres = the memory.** Stores history the ML needs + the portfolio.

```
Actions (cron) → ingest → normalize → embed+Haiku match → fee-aware net spread
   → snapshots/alerts → resolution capture → retention(dry-run) → docs/data.json
Pages → serves docs/ (index.html reads data.json; track-record.html reads track-record.json)
```

## Layout

```
src/ingest/    polymarket, kalshi, normalize (H2 category map), resolutions
src/match/     embed (MiniLM), align_agent (Haiku, cached), confidence
src/arb/       fees, spread, match_naive (sanity bridge)
src/storage/   db, schema.sql, writes, retention (dry-run by default)
src/ml/        snapshots+alerts, classifier (Phase B), convergence (Phase C)
src/analytics/ track_record
src/briefing/  portfolio, prices, news, summarize (Haiku), email_send
src/scanner_run.py   src/briefing_run.py   labeling/export_candidates.py
docs/          index.html, track-record.html, app.js, style.css
.github/workflows/   scanner.yml (every 4h), briefing.yml (08:00/20:00 CT)
tests/         7 offline suites (no network/DB/keys needed)
```

## Setup (only you can do this — H1)

Create these as **GitHub Actions Secrets** (Settings → Secrets and variables →
Actions). None ever go in the repo.

| Secret | From |
|---|---|
| `DATABASE_URL` | neon.tech free Postgres connection string |
| `ANTHROPIC_API_KEY` | console.anthropic.com (a few $ credit) |
| `ALPHAVANTAGE_KEY` | alphavantage.co (free) |
| `FINNHUB_KEY` | finnhub.io (free) |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | Google account → 2FA → App password |
| `BRIEFING_TO` | where briefings are sent |

Then: make the repo **public**, enable Pages (Settings → Pages → deploy from
branch `main`, folder `/docs`). The scanner secrets alone are enough to start;
briefing secrets can come later.

## Run

```bash
pip install -r requirements.txt
python -m src.storage.db          # apply schema (needs DATABASE_URL)
python -m src.scanner_run         # one scan -> docs/data.json
python -m src.analytics.track_record   # -> docs/track-record.json
python -m src.briefing_run        # sends only if within the local window
python tests/test_arb.py          # offline suites; also test_normalize, test_match,
                                  # test_snapshots, test_resolutions, test_pipeline_naive, test_final
```

Cost: hosting is free; AI is ~$2–3/month (Haiku, with alignment caching).

## Human checkpoints (H1–H11)

These need human judgment or action and are marked in code:

- **H1** setup/secrets (above) · **H2** verify category→fee-bucket map in
  `ingest/normalize.py` · **H3** tune match thresholds (0.60 / 0.80) on real
  data · **H4** spot-check alignment-agent JSON · **H5** validate fee
  constants against each platform's live calculator · **H6** confirm briefings
  land at 08:00/20:00 across a DST boundary · **H7** hand-label ~200–300 pairs
  (`labeling/export_candidates.py`) · **H8** fix the convergence epsilon/horizon
  · **H9** confirm retention windows + rollup **before** flipping it off
  dry-run (deletion is irreversible) · **H10** spot-check captured resolutions
  · **H11** lock track-record definitions up front; failed/expired signals are
  always counted.

## Honest limitations

- Cross-platform matching is imperfect even with an LLM check; low-confidence
  pairs are quarantined, not shown as facts.
- Fee formulas are close approximations and must be validated (H5).
- "Arbitrage" here has real frictions the tool can't see (settlement timing,
  KYC, capital lockup, differing resolution criteria). It surfaces *signal*;
  acting is a separate manual decision.
- Not financial advice.
```
