-- Phase 1 foundation: named schemas (ADR Domain 2) + ingestion plumbing (ADR Domain 1)
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS scoring;
CREATE SCHEMA IF NOT EXISTS valuation;
CREATE SCHEMA IF NOT EXISTS signals;
CREATE SCHEMA IF NOT EXISTS sim;
CREATE SCHEMA IF NOT EXISTS draft;

CREATE TABLE IF NOT EXISTS raw.ingest_runs (
    run_id      SERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','success','failed')),
    row_count   INTEGER,
    schema_hash TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_source ON raw.ingest_runs(source, started_at DESC);

CREATE TABLE IF NOT EXISTS raw.sleeper_projections (
    snapshot_id SERIAL PRIMARY KEY,
    run_id      INTEGER REFERENCES raw.ingest_runs(run_id),
    season      INTEGER NOT NULL,
    week        INTEGER,                -- NULL = season-level projection
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB NOT NULL          -- full API response, untouched
);

CREATE TABLE IF NOT EXISTS raw.nflverse_player_week (
    gsis_id                 TEXT NOT NULL,
    season                  INTEGER NOT NULL,
    week                    INTEGER NOT NULL,
    player_name             TEXT,
    position                TEXT,
    team                    TEXT,
    completions             INTEGER,
    attempts                INTEGER,
    passing_yards           REAL,
    passing_tds             INTEGER,
    passing_first_downs     INTEGER,
    interceptions           INTEGER,
    carries                 INTEGER,
    rushing_yards           REAL,
    rushing_tds             INTEGER,
    rushing_first_downs     INTEGER,
    receptions              INTEGER,
    targets                 INTEGER,
    receiving_yards         REAL,
    receiving_tds           INTEGER,
    receiving_first_downs   INTEGER,
    punt_return_yards       REAL,
    kickoff_return_yards    REAL,
    fumbles_lost            INTEGER,
    fetched_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gsis_id, season, week)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_league_settings (
    league_key  TEXT PRIMARY KEY,
    season      INTEGER NOT NULL,
    league_name TEXT,
    num_teams   INTEGER,
    renew       TEXT,      -- '{game_id}_{league_id}' of PREVIOUS season, '' if none
    renewed     TEXT,      -- next season's pointer
    qb_slots    INTEGER,   -- starting QB slots (2QB detection, risk R4)
    roster_positions JSONB,
    managers    JSONB,     -- {manager_guid: nickname} for the season (R9 continuity)
    settings_payload JSONB NOT NULL,   -- full settings response, untouched
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.yahoo_player_week (
    league_key      TEXT NOT NULL,
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    yahoo_player_id TEXT NOT NULL,
    total_points    NUMERIC,
    stats           JSONB NOT NULL,    -- raw stat list from API
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, week, yahoo_player_id)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_standings (
    league_key   TEXT NOT NULL,
    team_key     TEXT NOT NULL,
    season       INTEGER NOT NULL,
    team_name    TEXT,
    final_rank   INTEGER,
    payload      JSONB NOT NULL,     -- full standings entry (W/L, PF/PA, manager info)
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, team_key)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_matchups (
    league_key   TEXT NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    payload      JSONB NOT NULL,     -- full scoreboard response; parsed in Phase 2
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, week)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_transactions (
    league_key      TEXT NOT NULL,
    transaction_key TEXT NOT NULL,
    season          INTEGER NOT NULL,
    type            TEXT,             -- add, drop, add/drop, trade, commish
    ts              TIMESTAMPTZ,
    payload         JSONB NOT NULL,   -- full transaction incl players, teams, waiver detail
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, transaction_key)
);

CREATE TABLE IF NOT EXISTS public.player_id_xwalk (
    xwalk_id        SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    position        TEXT,
    team            TEXT,
    gsis_id         TEXT,
    sleeper_id      TEXT,
    yahoo_id        TEXT,
    fantasypros_id  TEXT,
    manual_override BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_xwalk_yahoo ON public.player_id_xwalk(yahoo_id);
CREATE INDEX IF NOT EXISTS idx_xwalk_sleeper ON public.player_id_xwalk(sleeper_id);
