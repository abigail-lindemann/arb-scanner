# Build Guide — Prediction Market Arbitrage Scanner + Daily Market Briefing Agent

**Audience:** an LLM / coding agent implementing this project.
**Primary language:** Python. **Hosting:** GitHub Pages (static dashboard) + GitHub Actions (all compute). **Database:** Neon Postgres (free tier).
**Repo is PUBLIC.** This is a hard constraint that governs every decision below.

---

## 0. Non-negotiable rules

1. **No secrets in the repo, ever.** API keys, the database connection string, the email app password, and the Anthropic key live only in **GitHub Actions Secrets** and are read via `os.environ`. Provide a `.env.example` with empty placeholders; never a real `.env`.
2. **No portfolio data in the repo, ever.** Portfolio holdings live in the Postgres `portfolio` table, never in a committed file.
3. **Informational only.** This tool never places trades and never emits personalized investment advice. It surfaces data and computed signals. Keep all user-facing copy framed as information, not recommendations to buy/sell.
4. **GitHub Pages is static.** The browser never calls the market APIs and never holds a secret. All fetching, ML, and email happen in GitHub Actions Python. The Action writes `docs/data.json`; the static site only reads it.
5. **Fail soft.** A single market or API hiccup must not kill a whole run. Wrap per-item work in try/except, log, and continue.

---

## 0.5 Human checkpoints — where a person must verify or act

Several steps require human judgment, real-world verification, or an action only a person can take (creating accounts, labeling data, confirming external facts). **Do not silently assume defaults at these points — stop, surface the question, and get the human's input or confirmation.** Each is marked inline below with a `👤 HUMAN CHECK` callout. Summary:

| # | Where | What the human must do | Why it can't be automated |
|---|---|---|---|
| H1 | §3 Phase 0 | Create accounts, generate keys, add GitHub Secrets, enable Pages | Only the account owner can do this; agent must never invent or commit credentials |
| H2 | §4.3 Normalize | Verify the category mapping from each platform's raw categories to the fee-table buckets | Platform category names differ and change; a wrong mapping silently corrupts the fee math |
| H3 | §5.1 / §5.3 | Tune the candidate threshold (0.60) and high-confidence threshold (0.80) after seeing real match quality | Right values depend on observed precision/recall, not knowable in advance |
| H4 | §5.2 | Spot-check a sample of the alignment agent's JSON outputs before trusting it at scale | LLM alignment can be wrong; bad alignments produce phantom arbitrage |
| H5 | §7.1 Fees | Validate the fee constants/formulas against each platform's live fee calculator | Fees change and the closed-form curves are approximations; the whole net-spread signal depends on these |
| H6 | §10–§11 | Confirm briefing emails actually arrive at 08:00/20:00 local across a DST boundary | Cron is UTC; only a real send across the boundary proves the tz logic |
| H7 | §12 Phase B | Hand-label ~200–300 candidate pairs (`same_event` 1/0) | Supervised training needs ground-truth labels a human provides |
| H8 | §12 Phase C | Choose the convergence label definition (epsilon and N days) | A modeling judgment call about what "converged" means for your use |
| H9 | §6.5 Retention | Confirm the retention window and that rollups preserve ML features *before* any raw rows are deleted | Deletion is irreversible; the free DB tier forces pruning, but the convergence model needs the history |
| H10 | §8.5 Resolution capture | Spot-check that detected market resolutions match real-world outcomes | Resolution fields differ across platforms; a wrong capture poisons both the track record and the convergence labels |
| H11 | §9.5 Track-record | Fix the "hit"/epsilon/horizon definitions *before* measuring, and report failed/expired signals honestly | A track record is only meaningful if it's defined up front with no look-ahead or cherry-picking |

---

## 1. Architecture

```
                         GitHub Actions (cron)
                         ─────────────────────
  Polymarket Gamma/CLOB ─┐
  Kalshi public API     ─┤→ ingest → normalize → unified DataFrame
                         │      │
                         │      ├→ embeddings + LLM alignment → matched pairs
                         │      ├→ fee-aware net-spread + inversion → opportunities
                         │      ├→ write snapshot rows ─────────────→ Neon Postgres
                         │      └→ write docs/data.json + git commit
                         │
                         └→ (separate cron) briefing: portfolio P&L + news
                                  + Haiku summary → email via Gmail SMTP

  GitHub Pages (static)  ←── serves docs/  (index.html reads data.json)
```

Two independent workflows: `scanner.yml` (every 4h) and `briefing.yml` (08:00 & 20:00 America/Chicago).

---

## 2. Repository layout

```
repo/
  docs/                      # GitHub Pages root
    index.html
    track-record.html        # signal performance page
    app.js
    style.css
    data.json                # generated by scanner_run.py
    track-record.json        # generated by analytics/track_record.py
  src/
    ingest/
      polymarket.py
      kalshi.py
      normalize.py
      resolutions.py         # captures final market outcomes (§8.5)
    match/
      embed.py
      align_agent.py
      confidence.py
    arb/
      fees.py
      spread.py
    storage/
      db.py
      schema.sql
      retention.py           # prune + roll up old snapshots (§6.5)
    ml/
      snapshots.py
      classifier.py          # phase 2 (after labeling)
      convergence.py         # phase 3 (after data accrues)
    analytics/
      track_record.py        # scores past signals → track-record.json (§9.5)
    briefing/
      portfolio.py
      prices.py
      news.py
      summarize.py
      email_send.py
    scanner_run.py
    briefing_run.py
  labeling/
    export_candidates.py     # writes label_pairs.csv for human labeling
  .github/workflows/
    scanner.yml
    briefing.yml
  requirements.txt
  README.md
  .env.example
```

`requirements.txt` (pin reasonable versions): `requests`, `pandas`, `numpy`, `sentence-transformers`, `psycopg[binary]`, `anthropic`, `yfinance`, `python-dateutil`, `scikit-learn`, `tenacity`.

---

## 3. Phase 0 — Setup

**Secrets to create in the repo (Settings → Secrets and variables → Actions):**
- `DATABASE_URL` — Neon Postgres connection string (`postgresql://...`).
- `ANTHROPIC_API_KEY`
- `ALPHAVANTAGE_KEY`
- `FINNHUB_KEY`
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
- `BRIEFING_TO` — recipient email.

**Enable GitHub Pages:** Settings → Pages → Source = "Deploy from a branch", branch `main`, folder `/docs`.

**`storage/db.py`:** single helper returning a `psycopg` connection from `os.environ["DATABASE_URL"]`. Run `schema.sql` once at startup if tables don't exist (`CREATE TABLE IF NOT EXISTS`).

> 👤 **HUMAN CHECK (H1) —** All of the above accounts, keys, and secrets must be created by the human. The agent must **not** fabricate credentials, hardcode them, or commit them. If a required secret is missing at runtime, fail with a clear message naming the missing secret rather than guessing. Pause and ask the human to complete this step before proceeding.

---

## 4. Phase 1 — Data ingestion → unified DataFrame

### 4.1 Polymarket (`ingest/polymarket.py`)
- **Gamma API**, fully public, no auth. Base: `https://gamma-api.polymarket.com`.
  - `GET /markets?active=true&closed=false&order=volume24hr&ascending=false&limit=...` to pull the most active markets. Page through to collect the **top ~300 by 24h volume**.
  - Each market includes: `question`/`title`, `description`, `outcomes`, `outcomePrices` (decimal strings, e.g. `"0.62"` = 62%), `volume24hr`, `liquidity`, `endDate`, category/tags, and CLOB token IDs.
- **Bid/ask** (for the actionable tables): use the public CLOB price endpoints. Base: `https://clob.polymarket.com`. `GET /price?token_id=<id>&side=buy` and `side=sell` give executable prices; `GET /midpoint?token_id=<id>` gives mid. Fetch these only for matched markets to limit calls.
- Prices are **decimals 0–1**. Keep them as probabilities directly.

### 4.2 Kalshi (`ingest/kalshi.py`)
- **Public market-data endpoints, no auth required** (only trading needs RSA keys — we never trade). Base: `https://external-api.kalshi.com/trade-api/v2` (fall back to `https://trading-api.kalshi.com/trade-api/v2` if needed).
  - `GET /markets?status=open&limit=100` with cursor pagination (`cursor` in response). Collect **top ~300 by `volume_24h`**.
  - Each market includes: `ticker`, `event_ticker`, `title`, `subtitle`, `yes_bid`, `yes_ask`, `no_bid`, `no_ask`, `last_price`, `volume`, `volume_24h`, `open_interest`, `status`, `close_time`, category/series info.
- Prices are **cents 1–99**. Divide by 100 to get probabilities. Mid = `(yes_bid + yes_ask) / 2 / 100`.

### 4.3 Normalize (`ingest/normalize.py`)
Map both platforms into one DataFrame with this schema (one row per **outcome-level tradeable probability**, plus event grouping):

| col | meaning |
|---|---|
| `platform` | `"polymarket"` / `"kalshi"` |
| `market_id` | platform id (`conditionId`/slug or `ticker`) |
| `event_id` | platform event id |
| `title` | human question text |
| `description` | full resolution text (used by alignment agent) |
| `category` | normalized category (see fee table) |
| `outcomes` | list of outcome labels |
| `prob_mid` | midpoint probability 0–1 |
| `prob_yes_bid`, `prob_yes_ask` | executable probabilities 0–1 |
| `volume_24h` | float |
| `liquidity` | float (use OI for Kalshi if liquidity absent) |
| `end_date` | ISO datetime |
| `status` | open/closed |

Write every normalized row to the `markets_raw` snapshot table (Section 6) with the run timestamp.

> 👤 **HUMAN CHECK (H2) —** The `category` field must map each platform's *raw* category/tag names onto the fee-table buckets in §7.1 (crypto, economics, politics, sports, geopolitics, etc.). Polymarket and Kalshi name categories differently and change them over time. Build the mapping explicitly, then have the human eyeball a sample to confirm markets land in the right bucket — a mis-mapped category silently applies the wrong fee and corrupts every net-spread for that market. Default unmapped categories to the most conservative (highest) fee and log them for human review.

**Acceptance:** running ingestion prints a DataFrame of ~600 rows (≈300 per platform) with no nulls in `title`, `prob_mid`, `category`.

---

## 5. Phase 2 — Matching

### 5.1 Embeddings (`match/embed.py`)
- Model: `sentence-transformers` `all-MiniLM-L6-v2` (384-dim, CPU-fast, fine in Actions).
- Embed each platform's `title` (+ first sentence of `description`). Compute cross-platform cosine similarity (Polymarket × Kalshi).
- Generate **candidate pairs** where cosine ≥ **0.60**. Keep top few candidates per Polymarket market.

### 5.2 LLM alignment agent (`match/align_agent.py`)
For each candidate pair, call **Claude Haiku** (`model="claude-haiku-4-5"`) with both titles, descriptions, and outcome lists. Require a **JSON-only** response (no prose, no markdown fences):

```json
{
  "same_event": true,
  "outcome_map": [{"polymarket": "Yes", "kalshi": "Yes"}],
  "inverted": false,
  "resolution_match_score": 0.0_to_1.0,
  "notes": "short reason"
}
```

- `same_event`: do these resolve on the same real-world outcome?
- `outcome_map`: align each Polymarket outcome to its Kalshi counterpart (handles multi-outcome).
- `inverted`: true if Polymarket-Yes ≈ Kalshi-No.
- `resolution_match_score`: penalize different resolution criteria / dates / data sources.

**Cache every result in `matched_pairs` keyed by (pm_market_id, kalshi_market_id).** On later runs, only call Haiku for pairs not already in the table. This keeps ongoing LLM cost near zero.

### 5.3 Confidence (`match/confidence.py`)
Composite confidence:
```
confidence = 0.5*embedding_sim + 0.3*resolution_match_score + 0.2*(1 if same_event else 0)
```
Only keep pairs with `same_event == true` and `confidence ≥ 0.80` for the high-confidence views; keep lower-confidence ones flagged for the "review" surface. Binary pairs will dominate the high-confidence set; multi-outcome pairs are supported but expected to land lower.

**Acceptance:** matched_pairs table populates; re-running does NOT re-call Haiku for already-aligned pairs (verify via a call counter/log).

---

## 6. Phase 3 — Storage (Neon Postgres) — `storage/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS markets_raw (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  platform TEXT, market_id TEXT, event_id TEXT,
  title TEXT, description TEXT, category TEXT,
  outcomes JSONB, prob_mid DOUBLE PRECISION,
  prob_yes_bid DOUBLE PRECISION, prob_yes_ask DOUBLE PRECISION,
  volume_24h DOUBLE PRECISION, liquidity DOUBLE PRECISION,
  end_date TIMESTAMPTZ, status TEXT
);

CREATE TABLE IF NOT EXISTS matched_pairs (
  id BIGSERIAL PRIMARY KEY,
  pm_market_id TEXT, kalshi_market_id TEXT,
  embedding_sim DOUBLE PRECISION,
  outcome_map JSONB, inverted BOOLEAN,
  resolution_match_score DOUBLE PRECISION,
  same_event BOOLEAN, confidence DOUBLE PRECISION,
  created_at TIMESTAMPTZ DEFAULT now(),
  last_seen TIMESTAMPTZ,
  UNIQUE (pm_market_id, kalshi_market_id)
);

CREATE TABLE IF NOT EXISTS spread_snapshots (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  pair_id BIGINT REFERENCES matched_pairs(id),
  category TEXT,
  pm_mid DOUBLE PRECISION, kalshi_mid DOUBLE PRECISION,
  gross_spread DOUBLE PRECISION,   -- |pm_mid - kalshi_mid| (inversion-adjusted)
  net_spread DOUBLE PRECISION      -- after taker fees both legs
);

CREATE TABLE IF NOT EXISTS portfolio (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL,
  shares DOUBLE PRECISION NOT NULL,
  avg_cost DOUBLE PRECISION NOT NULL,
  purchase_date DATE
);

CREATE TABLE IF NOT EXISTS market_resolutions (
  id BIGSERIAL PRIMARY KEY,
  platform TEXT, market_id TEXT,
  resolved_outcome TEXT,          -- winning outcome label ("Yes"/"No"/multi-outcome label)
  final_prob DOUBLE PRECISION,    -- last observed probability before close
  resolved_at TIMESTAMPTZ,
  detected_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (platform, market_id)
);

CREATE TABLE IF NOT EXISTS signal_log (
  id BIGSERIAL PRIMARY KEY,
  pair_id BIGINT REFERENCES matched_pairs(id),
  category TEXT,
  first_flagged_at TIMESTAMPTZ,        -- when net_spread first crossed the 5% threshold
  flagged_net_spread DOUBLE PRECISION,
  status TEXT,                         -- 'open' | 'converged' | 'resolved' | 'expired'
  converged BOOLEAN, converged_at TIMESTAMPTZ, days_to_converge DOUBLE PRECISION,
  resolved BOOLEAN, outcome_consistent BOOLEAN,  -- did both legs resolve the same real-world way
  UNIQUE (pair_id, first_flagged_at)
);

CREATE TABLE IF NOT EXISTS spread_snapshots_daily (
  id BIGSERIAL PRIMARY KEY,
  day DATE, pair_id BIGINT, category TEXT,
  net_open DOUBLE PRECISION, net_close DOUBLE PRECISION,
  net_min DOUBLE PRECISION, net_max DOUBLE PRECISION,
  UNIQUE (day, pair_id)
);
```

`spread_snapshots` is the dataset the convergence model trains on later — write it every run from the first run onward.

---

## 6.5 Phase 3b — Storage retention & rollup (`storage/retention.py`)

Neon's free tier is ~0.5 GB. Writing ~600 `markets_raw` rows and hundreds of `spread_snapshots` rows every 4 hours fills that in a few months, so retention is a v1 requirement, not an afterthought. Run `retention.py` at the end of each scanner run (or as a separate weekly workflow).

Policy (tune the windows):
- `markets_raw`: keep ~14 days at full resolution, then delete. It's a debugging/audit trail, not training data.
- `spread_snapshots`: keep ~30 days at full (4-hourly) resolution. Older than that, **roll up to one row per pair per day** into `spread_snapshots_daily` (open/close/min/max net spread), then delete the fine-grained rows. The daily rollup is tiny and kept indefinitely — it preserves the spread *trajectory* the convergence model needs.
- `signal_log`, `market_resolutions`, `spread_snapshots_daily`: small; keep forever.

> 👤 **HUMAN CHECK (H9) —** This phase **permanently deletes rows**. Before enabling deletion, the human must confirm (a) the retention windows, and (b) that the daily rollup preserves every feature the convergence model (§12 Phase C) consumes — once raw snapshots are gone they can't be recovered. Recommend running retention in a "dry-run / log-only" mode first and having the human inspect what *would* be deleted before flipping it live.

---

## 7. Phase 4 — Arbitrage logic

### 7.1 Fees (`arb/fees.py`)
Implement fees as a **configurable, validated module**. Both platforms' fees peak near 50% probability and taper toward the extremes. Make the constants easy to tune and **validate against each platform's live fee calculator**; do not treat the formulas below as exact.

```python
# Polymarket: category max fee (as fraction of $1 notional, at p=0.5), V2 (2026)
PM_CATEGORY_MAX = {
    "crypto": 0.0180,
    "economics": 0.0125, "culture": 0.0125, "weather": 0.0125, "other": 0.0125,
    "politics": 0.0100, "finance": 0.0100, "tech": 0.0100, "mentions": 0.0100,
    "sports": 0.0075,
    "geopolitics": 0.0, "world": 0.0,
}

def pm_fee(prob, category):
    cap = PM_CATEGORY_MAX.get(category, 0.0125)
    return cap * (4 * prob * (1 - prob))   # peaks at cap at p=0.5, symmetric

def kalshi_fee(prob):
    # taker fee per contract ≈ 0.07 * p * (1-p)  (peaks ~0.0175 at p=0.5)
    return 0.07 * prob * (1 - prob)
```
Default to **taker fees on both legs** (conservative; assumes market orders). Expose a `maker=False` flag — makers pay zero on both platforms — so a future toggle can show the maker-side edge.

### 7.2 Net spread + inversion (`arb/spread.py`)
For each matched pair:
1. Align the comparison using `outcome_map`/`inverted` (if inverted, compare `pm_yes` to `kalshi_no = 1 - kalshi_yes`).
2. `gross_spread = abs(pm_prob - kalshi_prob)` on the aligned outcome.
3. To capture a real edge you buy the cheaper side and the complementary side on the other platform; subtract taker fees on both legs at their respective prices:
   `net_spread = gross_spread - pm_fee(pm_prob, category) - kalshi_fee(kalshi_prob)`.
4. Use **`prob_mid`** for the scatter/agreement visual; use **bid/ask** (`prob_yes_ask` on the buy leg) for the actionable net spread in the tables.
5. Flag an "opportunity" when `net_spread ≥ 0.05` (your 5% threshold, net of fees). Attach `resolution_match` warning if `resolution_match_score < 0.8`.
6. Dollar illustration at standard position size **$100**: `est_edge_usd = net_spread * 100`.

**Acceptance:** opportunities list shows gross vs net spread; a high-fee crypto market near 50¢ with a 6% gross gap correctly shows a much smaller (often sub-threshold) net.

---

## 8. Phase 5 — Snapshots & drastic-move alerts (`ml/snapshots.py`)

- Every scanner run writes a `spread_snapshots` row per matched pair.
- After writing, compare each pair's `net_spread` to its most recent prior snapshot. If `abs(net_spread_now - net_spread_prev) ≥ 0.10` (10 percentage points), queue an **alert email** (reuse `briefing/email_send.py`) summarizing the pair, old→new spread, both prices, and links. Subject like `⚠ Spread move: <event>`.
- Keep alerts deduped (don't re-alert the same move on the next run if it hasn't moved further).

---

## 8.5 Phase 5b — Resolution capture (`ingest/resolutions.py`)

This is the piece that lets the tool prove itself: when a matched market actually settles, record how it resolved. Without this, you never learn whether a flagged spread was real, and the convergence model (§12 Phase C) has no ground truth.

Each scanner run:
1. Find markets referenced by `matched_pairs` that have left the open feed or whose `status` flipped to closed/settled/finalized. On **Polymarket**, watch the market's resolved/closed flags and `outcomePrices` collapsing toward 0/1 (and `umaResolutionStatus` where present). On **Kalshi**, `status` becomes settled/finalized and the `result` field names the winning side.
2. Write the final outcome to `market_resolutions` (idempotent on `(platform, market_id)`).
3. Update `signal_log` for each pair: record when its net spread first crossed the 5% threshold (`first_flagged_at`), whether the spread later converged below an epsilon (e.g. 0.02) — `converged`, `days_to_converge` — and, once both legs resolve, whether they resolved to the **same real-world outcome** (`outcome_consistent`). That last flag is the strongest possible validation that the match was genuine, not a phantom.

> 👤 **HUMAN CHECK (H10) —** Resolution/settlement fields are inconsistent across the two platforms and occasionally ambiguous (voided markets, partial resolutions, multi-outcome winners). The human must spot-check a sample of captured resolutions against the actual real-world outcome before this data is trusted — a wrong capture silently corrupts both the track record (§9.5) and the convergence labels (§12 Phase C). Log low-confidence captures (e.g. `outcome_consistent == false` on a high-confidence pair) for human review rather than accepting them blindly.

**Acceptance:** when a tracked market settles, a `market_resolutions` row appears and the corresponding `signal_log` row gets `resolved=true` with a populated `outcome_consistent`.

---

## 9. Phase 6 — Static dashboard (`docs/`)

Theme: **financial-terminal**, dark background (#0a0e0f-ish), monospace fonts, **neon green** accents, color-coded points.

### 9.1 `scanner_run.py` writes `docs/data.json`
```json
{
  "generated_at": "ISO",
  "events": [
    {"pair_id": 1, "title_pm": "...", "title_kalshi": "...", "category": "politics",
     "pm_mid": 0.62, "kalshi_mid": 0.66, "pm_bidask": [0.61,0.63], "kalshi_bidask":[0.65,0.67],
     "gross_spread": 0.04, "net_spread": 0.028, "est_edge_usd": 2.8,
     "confidence": 0.86, "inverted": false, "resolution_warning": false,
     "volume_pm": 12000, "volume_kalshi": 8000, "end_date": "...",
     "link_pm": "https://polymarket.com/...", "link_kalshi": "https://kalshi.com/...",
     "spread_history": [{"ts":"...","net":0.02}, ...]}
  ]
}
```

### 9.2 `app.js` (vanilla + Chart.js via CDN)
- **Scatter** (Chart.js scatter): x = Polymarket mid, y = Kalshi mid, plus a `y=x` reference line. **Distance from the line = the difference score** (perfect agreement on the line). Color points by difference score (on-line/low = neutral green, far = bright/hot). Inverted pairs plotted against `1 - kalshi`.
- **Click a point → KPI card** showing: both prices (mid + bid/ask), gross & net spread, est. $ edge at $100, volume/liquidity each side, category, end date, confidence, resolution-mismatch warning, inversion flag, a small spread-over-time sparkline, and links to both markets.
- **Three ranked tables:** (a) **Best net ROI** (by `net_spread` desc), (b) **Most likely to converge** (by convergence score once available; until then hide or sort by spread velocity), (c) **Highest confidence** (by `confidence` desc).
- **Filters:** category, min net spread, min liquidity, min confidence, "hide resolution mismatches" toggle.
- No localStorage/sessionStorage. All state in JS memory.

**Acceptance:** opening the Pages URL renders the scatter, points are clickable, tables sort/filter, theme is dark/neon-green/monospace.

---

## 9.5 Phase 6b — Signal track-record page (`analytics/track_record.py` + `docs/track-record.html`)

This is the single highest-value addition for a portfolio project: it answers "when the scanner flagged a >5% net spread, what actually happened?" It turns the tool from "here are some signals" into "here's how the signals have performed."

`analytics/track_record.py` reads `signal_log` (+ `spread_snapshots`/`_daily`) and computes, writing `docs/track-record.json`:
- **Hit rate** — share of flagged signals that converged below epsilon before resolution.
- **Median days-to-converge**, and a histogram of the distribution.
- **Captured vs. realized** — flagged net spread vs. how much actually closed.
- **Match quality** — share of resolved pairs with `outcome_consistent == true` (did the two legs really resolve the same way).
- Breakdowns by **category** and **confidence bucket**, each with its sample size.

`docs/track-record.html` renders these as summary cards + a category/confidence table + the days-to-converge histogram, in the same financial-terminal theme, linked as a second tab from `index.html`. It reads only `track-record.json` — no compute in the browser.

> 👤 **HUMAN CHECK (H11) —** A track record is only honest if it's defined *before* it's measured. The human must lock the definitions up front — what counts as a "hit," the convergence epsilon, the evaluation horizon — and the implementation must avoid look-ahead (score each signal only on data available at and after `first_flagged_at`, never using the resolution to retroactively pick the flag point). Always display sample sizes and **include expired/failed signals** — never quietly drop the losers. Small-n buckets should be labeled as such, not presented as conclusions.

**Acceptance:** the track-record page loads with real metrics once some signals have resolved; numbers reconcile against a manual spot-check of a few `signal_log` rows; failed and expired signals are visibly counted.

---

## 10. Phase 7 — GitHub Actions

### `.github/workflows/scanner.yml`
- `on: schedule: - cron: "0 */4 * * *"` (every 4h) + `workflow_dispatch`.
- Steps: checkout → setup-python 3.12 → `pip install -r requirements.txt` → run `python -m src.scanner_run` (reads secrets from env) → commit `docs/data.json` back to `main` (use a bot commit; `git config` + `git commit -am` + `git push`, guarded with `[skip ci]`).
- Pass secrets as `env:` from `${{ secrets.* }}`.

### `.github/workflows/briefing.yml`
- `on: schedule:` two crons at `0 13 * * *` and `0 1 * * *` (UTC ≈ 08:00 & 20:00 CDT). **Handle DST in code:** schedule slightly early and, in `briefing_run.py`, compute current time in `zoneinfo.ZoneInfo("America/Chicago")` and only send if within the intended window; otherwise exit. This avoids the UTC/DST drift.
- Steps: checkout → python → install → `python -m src.briefing_run`.

**Acceptance:** `workflow_dispatch` manual run of each workflow completes green; scanner commit updates the live dashboard; briefing sends one email.

---

## 11. Phase 8 — Briefing agent (`src/briefing/`)

- `portfolio.py`: read `portfolio` table. If empty, briefing still runs on market/news only (graceful).
- `prices.py`: current prices via `yfinance` (free); fall back to Alpha Vantage `GLOBAL_QUOTE`. Compute per-holding unrealized P&L = `(price - avg_cost) * shares` and totals.
- `news.py`: **Alpha Vantage `NEWS_SENTIMENT`** (free; ticker-tagged sentiment) for each holding + general market; supplement with **Finnhub** company news. Dedupe by URL/title. Keep top items by relevance/recency.
- `summarize.py`: one **Haiku** (`claude-haiku-4-5`) call. Input: portfolio + P&L, top news with sentiment, and the scanner's current top opportunities. Output: a **written brief** with a **bullet summary at the top** ("the 3–5 things that matter this morning"), then short sections (your holdings & overnight moves, relevant news, top arbitrage signals, anything notable). Keep neutral/informational tone — describe, don't advise. Enable prompt caching on the stable system-prompt portion.
- `email_send.py`: `smtplib` over Gmail SMTP (`smtp.gmail.com:587`, STARTTLS) using `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD`, send to `BRIEFING_TO`. HTML email with the bullets up top.

**Acceptance:** a manual briefing run sends a readable HTML email with summary bullets, P&L (if portfolio populated), news, and scanner highlights.

---

## 12. Phase 9 — Machine learning (phased)

**Phase A — now:** embeddings matching (done in §5) + start writing `spread_snapshots` every run so the convergence dataset accrues from day one.

**Phase B — match classifier (`ml/classifier.py`), after ~200–300 labeled pairs:**
- `labeling/export_candidates.py` writes `label_pairs.csv` with columns: `pm_title, kalshi_title, pm_desc, kalshi_desc, embedding_sim, same_event(blank)`. The human labels `same_event` 1/0.
- Features: `embedding_sim`, end-date proximity, numeric/strike overlap, named-entity overlap (simple token overlap is fine to start), category match, `resolution_match_score`.
- Model: start with `LogisticRegression` or `GradientBoostingClassifier` (scikit-learn). Save with `joblib`. Replace/augment the §5.3 heuristic confidence with the model's probability. Report precision/recall; favor **precision** (fewer false matches).

**Phase C — convergence model (`ml/convergence.py`), after a few weeks of snapshots:**
- Label: did a pair's spread close below a small epsilon within N days of a given snapshot? (derive from `spread_snapshots` history).
- Features: current net spread, spread velocity (change over last K snapshots), pair age, volume each side, category, days-to-resolution.
- Model: gradient-boosted regressor/classifier (sklearn). Output a per-pair convergence score that powers the "Most likely to converge" table.

**Acceptance per phase:** classifier improves precision vs the heuristic on a held-out split; convergence model produces sensible scores and the table populates.

---

## 13. Implementation order (do in this sequence)

1. Phase 0 setup + `db.py` + `schema.sql`.
2. Phase 1 ingestion (both platforms) → unified DataFrame → write `markets_raw`.
3. Phase 4 fee/spread on a naive title-equality match (sanity check the math).
4. Phase 2 embeddings + alignment agent + confidence (with caching).
5. Phase 5 snapshots + alert path.
6. **Phase 5b resolution capture (§8.5) + Phase 3b retention/rollup (§6.5)** — turn both on early (retention in dry-run first) so the track-record dataset and storage hygiene start from day one.
7. Phase 6 `data.json` + dashboard.
8. **Phase 6b track-record page (§9.5)** — ships empty and fills in as signals resolve.
9. Phase 7 workflows (scanner first).
10. Phase 8 briefing agent + `briefing.yml`.
11. Phase 9 ML: snapshot logging is already on; add the classifier, then convergence (which now has resolution labels from step 6).

Build incrementally; each phase has an acceptance check — meet it before moving on.

---

## 14. Reference: verified facts (June 2026)

- Polymarket **Gamma API and Data API are fully public, no auth**; CLOB has public price endpoints (only order placement is authenticated). Gamma base `https://gamma-api.polymarket.com`; CLOB base `https://clob.polymarket.com`. CLOB migrated to V2 on 2026-04-28 (relevant only for trading, which we do not do).
- Kalshi **market-data endpoints require no authentication**; only trading/portfolio needs RSA-PSS keys. Base `https://external-api.kalshi.com/trade-api/v2`. Prices in cents (1–99); default page size 100; cursor pagination.
- Polymarket fees (V2, 2026): category-based taker fees peaking near 50%, max per 100 shares ≈ crypto 1.80%, econ/culture/weather/other 1.25%, politics/finance/tech/mentions 1.00%, sports 0.75%; geopolitics free; makers pay zero. **Validate constants against Polymarket's live fee calculator.**
- Kalshi taker fee ≈ `0.07 × price × (1−price)` per contract (peaks ~1.75% at 50¢); makers free. **Validate against Kalshi docs.**
- Claude Haiku 4.5 = $1 / $5 per million input/output tokens; model id `claude-haiku-4-5`. Use it for both the alignment agent and the briefing summary. Estimated total AI spend < $5/month given alignment caching.
- There is no longer a neutral unified cross-platform data API (Polymarket acquired Dome in early 2026), which is why this project builds its own matching layer.
