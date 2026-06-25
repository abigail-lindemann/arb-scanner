# Prediction Market Arbitrage Scanner + Daily Market Briefing
### Project overview, decisions, and setup — for Abi

This is the plain-English companion to the build guide. It explains *what* we're building, *why* each decision was made, *what it costs*, and *what you personally need to do*. The technical step-by-step lives in `BUILD_GUIDE_FOR_LLM.md`.

---

## What you're building, in one paragraph

Two connected tools that run themselves for free. **Tool 1** is an arbitrage scanner: a few times a day it pulls the most active markets from Polymarket and Kalshi, figures out which markets on each platform are betting on the *same* real-world event, and flags where the two platforms disagree on the odds by more than 5% *after fees*. You see this as a scatter plot (the closer a point sits to the diagonal, the more the platforms agree) with clickable detail cards and ranked opportunity tables. **Tool 2** is a daily briefing agent: twice a day it emails you a short written brief — your portfolio's overnight moves and P&L, the news that actually touches your holdings, and the scanner's best signals. Machine learning runs through both: semantic matching of events, a classifier that learns which matches are real, and a model that learns which price gaps actually close.

---

## Why this is a strong portfolio project

You said this is, at the end of the day, a portfolio project — so it's worth naming what it demonstrates. In one repo it shows: third-party **API integration** (two very different financial APIs), **data engineering** (normalizing messy cross-platform data into one schema), **NLP / embeddings** (semantic event matching), **LLM agents** (a structured-output alignment agent), **classical ML** (a match classifier and a convergence predictor on data you collect yourself), **full-stack deployment** (a live dashboard), and **automation / MLOps** (scheduled jobs, a database, a self-updating site) — all on **free, serverless infrastructure**. That combination is unusual and reads well.

---

## How the architecture works (the one thing worth understanding)

GitHub Pages only serves static files — it can't run Python, call APIs, or send email. So we split the system in two:

- **GitHub Actions is the engine.** It's a free scheduler that runs your Python on a timer. It does all the fetching, the ML, the database writes, and the emails. Your secrets live here safely.
- **GitHub Pages is the window.** It just displays a `data.json` file that the engine produces. No secrets, no API calls from the browser, nothing to attack.
- **Neon Postgres is the memory.** A free serverless database that stores the history the ML needs and holds your portfolio.

This is the whole trick that makes "Python + GitHub Pages + scheduled email + ML" possible for $0 of hosting.

---

## The decisions we made, and why

Because you value **informed, low-risk, high-reward** choices, here's the reasoning behind each call, not just the call.

**Informational only, no trading.** The tool never touches your money. The "reward" is the signal and the showcase; the "risk" is essentially zero because no capital is ever at stake and no trade is ever placed. This also keeps it clearly on the right side of "not financial advice."

**Net of fees, not gross.** A raw 6% gap between platforms can completely vanish after trading fees — and both platforms charge the most exactly where gaps look most tempting (near 50/50 odds). Showing only the *net* edge is the difference between a toy and an honest tool. The scanner computes fees per market based on its category and price.

**Midpoint for the picture, bid/ask for the money.** The scatter plot uses the clean midpoint price so the "do they agree?" visual isn't noisy. The opportunity tables use the real executable bid/ask, because actual arbitrage means paying to cross the spread on both sides. Two different prices for two different jobs.

**Conservative fee assumption (taker on both legs).** We assume the worst realistic case — market orders that pay full fees — so the tool never overstates an opportunity. Limit orders ("maker") pay zero fees on both platforms, so the real edge can be better; a future toggle can show that, but the default stays honest.

**Fewer, high-confidence matches.** Matching questions across platforms is the hard part and the main place a tool like this goes wrong. We'd rather show you 20 matches you can trust than 100 with junk mixed in. An LLM agent confirms each match and aligns the outcomes; low-confidence matches are flagged separately for review.

**Multi-outcome supported, but lower confidence at first.** Binary yes/no markets match cleanly. Multi-outcome events (e.g. "how many rate cuts") need each option aligned across platforms, which is genuinely harder — so those start as best-effort and improve as the classifier learns.

**Phased machine learning.** We don't bolt on ML for show. It arrives in three honest stages: (A) semantic matching now; (B) a match classifier once you've hand-labeled a few hundred examples, to cut false matches; (C) a convergence predictor once enough history has accumulated, to tell you which gaps actually close. Stage C costs nothing extra up front because we start logging the data it needs from the very first run.

**Paper portfolio first.** You don't have a portfolio yet, and you don't need one to build this. We start with a simple holdings list you control (ticker, shares, cost) so the whole briefing agent works end-to-end with zero real money and zero brokerage credentials. You populate real positions whenever you actually open them. This is both the lowest-risk path and the best engineering path.

**Scanner every 4 hours; alerts on big moves.** "A few times a day" without burning resources. On top of the schedule, if any matched gap swings by 10 points or more between checks, you get an immediate alert email — that's the "unless something drastic happens" you asked for.

---

## What it costs

Essentially free. GitHub Actions on a **public** repo gives unlimited minutes; GitHub Pages is free; Neon Postgres, Alpha Vantage, and Finnhub all have free tiers that comfortably cover personal use; Gmail SMTP is free. The only real cost is the AI:

- **Twice-daily briefing summary:** roughly **$1–2/month** (Claude Haiku at $1/$5 per million tokens).
- **Match-alignment agent:** a few cents per run, because each match is analyzed once and then cached — re-runs don't re-pay.

**Total: under ~$5/month, realistically $2–3.** You said a couple dollars a month is fine, so this fits.

---

## What only you can do (setup checklist)

I can't create accounts or hold your credentials, so these are yours. Each one becomes a **GitHub Actions Secret** (Settings → Secrets and variables → Actions → New repository secret). None of them ever go in the code.

Don't worry that you haven't done this before — it's copy-paste once and then never again.

1. **Create the GitHub repo (public)** and turn on Pages (Settings → Pages → deploy from branch `main`, folder `/docs`).
2. **Neon Postgres** (neon.tech) — free account, create a database, copy the connection string → secret `DATABASE_URL`.
3. **Anthropic API key** (console.anthropic.com) — add a few dollars of credit → secret `ANTHROPIC_API_KEY`.
4. **Alpha Vantage** free key (alphavantage.co) → secret `ALPHAVANTAGE_KEY`.
5. **Finnhub** free key (finnhub.io) → secret `FINNHUB_KEY`.
6. **Gmail app password** — in your Google account, enable 2-step verification, then create an "App password" for mail. Use your address → secret `GMAIL_ADDRESS`, the 16-char app password → secret `GMAIL_APP_PASSWORD`, and your destination address → secret `BRIEFING_TO`.

A GitHub Actions Secret is just an encrypted key/value the workflow can read at runtime; it's never visible in the repo, the logs, or the site. That's exactly why it's safe to keep this public.

When those six exist, every part of the system can run. There's no rush — you can add the briefing-agent secrets later and build the scanner first.

---

## Honest limitations (so the "informed" part is real)

- **Matching is imperfect.** Even with an LLM checking, some cross-platform pairs will be wrong or borderline. That's why low-confidence matches are quarantined and why the classifier exists.
- **Fee formulas are close, not exact.** Both platforms' fee math is parameterized in the code and should be checked against their live fee calculators; treat the net spread as a strong estimate, not a guarantee.
- **"Arbitrage" on prediction markets has real-world frictions** the scanner can't see: withdrawal/settlement timing, KYC, capital tied up until resolution, and the fact that two markets with identical-looking questions can resolve on different criteria (which is why mismatches get a warning badge). The tool surfaces *signal*; acting on it is a separate, manual decision you make with eyes open.
- **Not financial advice.** The briefing and the scanner describe data and computed signals. They don't tell you what to buy or sell — that's yours.

---

## What happens next

The build guide is written so a coding agent (or you) can implement it phase by phase, each with an acceptance check. Suggested order: scanner data pipeline → matching → dashboard → automation → briefing agent → the ML stages. The moment the scanner's first run lands, the convergence dataset starts accumulating in the background, so the most data-hungry ML is quietly working for you from day one.
