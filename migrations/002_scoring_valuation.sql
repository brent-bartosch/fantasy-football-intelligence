-- Phase 2: scoring engine, valuation, history-mining DDL (ADR Domain 2)

CREATE TABLE IF NOT EXISTS scoring.config (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    rules       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Engine output over historical actuals (recomputable from raw + config).
CREATE TABLE IF NOT EXISTS scoring.player_week_points (
    source          TEXT NOT NULL,      -- 'nflverse' | 'yahoo_engine'
    player_ref      TEXT NOT NULL,      -- gsis_id for nflverse, numeric yahoo id for yahoo_engine
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    points          NUMERIC NOT NULL,
    components      JSONB,              -- per-category breakdown for audit
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, player_ref, season, week, config_version)
);

-- Engine output over projection snapshots.
CREATE TABLE IF NOT EXISTS scoring.projection_points (
    source          TEXT NOT NULL,      -- 'sleeper' | 'fantasypros'
    snapshot_id     INTEGER NOT NULL,   -- raw.sleeper_projections.snapshot_id or raw.fp_snapshots.snapshot_id
    player_ref      TEXT NOT NULL,      -- source-native player id
    horizon         TEXT NOT NULL,      -- 'season' | 'week:N'
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    points          NUMERIC NOT NULL,
    components      JSONB,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, snapshot_id, player_ref, config_version)
);

-- DEF mapping: team defenses key on Yahoo numeric DEF ids + team abbr (carry-forward).
CREATE TABLE IF NOT EXISTS public.team_def_map (
    yahoo_def_id TEXT PRIMARY KEY,      -- numeric, e.g. '100012'
    team_abbr    TEXT NOT NULL UNIQUE,  -- canonical uppercase (ffi.ids.NFL_TEAMS)
    team_name    TEXT NOT NULL          -- Yahoo nickname, e.g. 'Chiefs'
);

-- Slot-vs-human annotation (user input; slot = Yahoo team number, stable per season).
CREATE TABLE IF NOT EXISTS public.manager_slot_annotations (
    league_slot  INTEGER NOT NULL,
    human_label  TEXT NOT NULL,
    from_season  INTEGER NOT NULL,
    to_season    INTEGER,               -- NULL = through present
    note         TEXT,
    PRIMARY KEY (league_slot, from_season)
);
INSERT INTO public.manager_slot_annotations (league_slot, human_label, from_season, note)
VALUES (12, 'Brent', 2021, 'user; joined 2021 (user-confirmed 2026-07-10; team Sp00rts 2021, Spœrts 2022+ — verified vs raw.yahoo_league_settings managers, which are unhidden from 2021 onward)')
ON CONFLICT (league_slot, from_season) DO NOTHING;

-- FantasyPros raw cache (one row per API call; the daily budget counts these).
CREATE TABLE IF NOT EXISTS raw.fp_snapshots (
    snapshot_id SERIAL PRIMARY KEY,
    run_id      INTEGER REFERENCES raw.ingest_runs(run_id),
    endpoint    TEXT NOT NULL,
    params      JSONB NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Valuation outputs (recomputable; params carry full provenance).
CREATE TABLE IF NOT EXISTS valuation.replacement_baseline (
    baseline_id     SERIAL PRIMARY KEY,
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    scenario        TEXT NOT NULL,      -- e.g. 'qb_hoard_0', 'qb_hoard_12'
    position        TEXT NOT NULL,
    replacement_rank INTEGER NOT NULL,
    replacement_points NUMERIC NOT NULL,
    params          JSONB NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS valuation.player_value (
    value_id        SERIAL PRIMARY KEY,
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    scenario        TEXT NOT NULL,
    xwalk_id        INTEGER NOT NULL REFERENCES public.player_id_xwalk(xwalk_id),
    position        TEXT NOT NULL,
    proj_points     NUMERIC NOT NULL,
    vorp            NUMERIC NOT NULL,
    tier            INTEGER,
    value_low       NUMERIC,            -- uncertainty band
    value_high      NUMERIC,
    params          JSONB NOT NULL,     -- snapshot ids, source weights, GMM params
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_player_value_lookup
    ON valuation.player_value (config_version, scenario, position, vorp DESC);

-- Parsed weekly H2H results (from raw.yahoo_matchups payloads — Task 13).
CREATE TABLE IF NOT EXISTS public.matchup_results (
    league_key   TEXT NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    team_key     TEXT NOT NULL,
    slot         INTEGER NOT NULL,
    points       NUMERIC NOT NULL,
    proj_points  NUMERIC,
    opp_team_key TEXT NOT NULL,
    opp_points   NUMERIC NOT NULL,
    is_playoffs  BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (league_key, week, team_key)
);

-- Slot column on teams (Yahoo team number within the league season).
ALTER TABLE teams ADD COLUMN IF NOT EXISTS slot INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS team_key VARCHAR(50);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_league_slot ON teams (league_id, slot);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_team_key ON teams (team_key);

-- Canonical numeric-yahoo-id -> one player row (players has one row per
-- game-code key; established fact #6). Latest game code = current name/team.
CREATE OR REPLACE VIEW public.v_player_yahoo_ids AS
SELECT DISTINCT ON (split_part(yahoo_player_id, '.p.', 2))
       split_part(yahoo_player_id, '.p.', 2) AS yahoo_id,
       player_id, player_name, position, nfl_team
FROM players
WHERE split_part(yahoo_player_id, '.p.', 2) ~ '^[0-9]+$'
ORDER BY split_part(yahoo_player_id, '.p.', 2),
         split_part(yahoo_player_id, '.p.', 1)::int DESC;
