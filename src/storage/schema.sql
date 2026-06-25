-- Neon Postgres schema. Idempotent: safe to run on every startup.
-- See BUILD_GUIDE §6 / §6.5 / §8.5 / §9.5.

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
CREATE INDEX IF NOT EXISTS idx_markets_raw_ts ON markets_raw (ts);
CREATE INDEX IF NOT EXISTS idx_markets_raw_platform_mid ON markets_raw (platform, market_id);

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
CREATE INDEX IF NOT EXISTS idx_spread_snapshots_pair_ts ON spread_snapshots (pair_id, ts);

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
  resolved BOOLEAN, outcome_consistent BOOLEAN,
  UNIQUE (pair_id, first_flagged_at)
);

CREATE TABLE IF NOT EXISTS spread_snapshots_daily (
  id BIGSERIAL PRIMARY KEY,
  day DATE, pair_id BIGINT, category TEXT,
  net_open DOUBLE PRECISION, net_close DOUBLE PRECISION,
  net_min DOUBLE PRECISION, net_max DOUBLE PRECISION,
  UNIQUE (day, pair_id)
);
