-- migrations/005_sim.sql — Phase 3: simulator results (ADR D8: results in tables, logs errors-only)
CREATE TABLE IF NOT EXISTS sim.batches (
    batch_id    SERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('farm','backtest')),
    git_sha     TEXT,
    config_version INTEGER NOT NULL,
    scenario    TEXT NOT NULL,
    season      INTEGER,                -- backtest year; NULL for farm (2026 pool)
    strategy    JSONB NOT NULL,         -- StrategyParams dump
    opponent_params JSONB NOT NULL,     -- tau, half_life, damp table, priors floor seasons
    n_drafts    INTEGER NOT NULL,
    seasons_per_draft INTEGER NOT NULL,
    base_seed   BIGINT NOT NULL,
    data_vintage JSONB NOT NULL,        -- {snapshot_id, snapshot_fetched_at, valuation_computed_at, priors_latest_season, degraded: bool}
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sim.batch_results (
    batch_id    INTEGER NOT NULL REFERENCES sim.batches(batch_id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,          -- 'all_play_pct','all_play_se','top3_rate','qb1_round_mean',...
    value       NUMERIC NOT NULL,
    PRIMARY KEY (batch_id, metric)
);
CREATE TABLE IF NOT EXISTS sim.sample_drafts (
    batch_id    INTEGER NOT NULL REFERENCES sim.batches(batch_id) ON DELETE CASCADE,
    draft_seed  BIGINT NOT NULL,
    reason      TEXT NOT NULL CHECK (reason IN ('worst','best','random')),
    our_position INTEGER NOT NULL,
    all_play_pct NUMERIC NOT NULL,
    picks       JSONB NOT NULL,         -- [{overall, slot, pos, ref, name}] x228
    our_roster  JSONB NOT NULL,
    PRIMARY KEY (batch_id, draft_seed, reason)
);
CREATE TABLE IF NOT EXISTS raw.backtest_sources (
    source      TEXT NOT NULL,          -- 'dynastyprocess','wayback_fp',...
    season      INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('adp','projections','ecr')),
    url         TEXT NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, season, kind)
);
CREATE TABLE IF NOT EXISTS sim.backtest_pool (
    season      INTEGER NOT NULL,
    ref         TEXT NOT NULL,          -- gsis_id (actuals join key)
    name        TEXT NOT NULL,
    position    TEXT NOT NULL,
    proj_points NUMERIC NOT NULL,
    vorp        NUMERIC NOT NULL,
    tier        INTEGER NOT NULL,
    adp         NUMERIC,
    degraded    BOOLEAN NOT NULL DEFAULT false,   -- synthetic projections (R11 fallback)
    provenance  JSONB NOT NULL,
    PRIMARY KEY (season, ref)
);
CREATE TABLE IF NOT EXISTS sim.backtest_reference (
    ref_id      SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    git_sha     TEXT,
    description TEXT NOT NULL,
    composite   NUMERIC NOT NULL,       -- mean our-seat all-play% over reference cells
    band        NUMERIC NOT NULL,       -- 2*SE noise band (ADR D7 gate)
    detail      JSONB NOT NULL,         -- per-cell values
    is_active   BOOLEAN NOT NULL DEFAULT true
);
