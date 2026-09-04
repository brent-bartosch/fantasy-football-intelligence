-- 014_inseason_ops.sql — in-season state + advisory output tables (ADR D2/D8).
--
-- These are the canonical stores the Plan 2 modules read/write:
--   league_transactions   one output schema for manual + Yahoo backfill (adapter.py)
--   league_rosters        roster snapshots, same two backends
--   market_trends         normalized from raw.sleeper_trending (usage/market.py)
--   waiver_move_events    event-sourced move-budget ledger (waiver/ledger.py)
--   push_log              the only place a push is recorded (scripts/notify.py)
--
-- `source` + `ts_precision` are attached at exactly one place (league_state) so
-- "success at the wrong precision" (R17) is detectable: manual captures carry
-- precision `date` or `exact`, Yahoo backfill carries `exact`. Every read joins
-- nothing — a reader sees the precision and can refuse to render on `unknown`.

CREATE TABLE IF NOT EXISTS public.league_transactions (
    tx_seq                bigserial PRIMARY KEY,
    league_id             integer NOT NULL,
    season                integer NOT NULL,
    week                  integer,
    kind                  text NOT NULL
                          CHECK (kind IN ('add','drop','add_drop','trade','commish')),
    ts                    timestamptz,
    ts_precision          text NOT NULL
                          CHECK (ts_precision IN ('exact','date','unknown')),
    source                text NOT NULL,
    source_transaction_id text NOT NULL,
    team_id               integer,
    payload               jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_league_transactions_week
    ON public.league_transactions (league_id, season, week);

CREATE TABLE IF NOT EXISTS public.league_rosters (
    roster_seq    bigserial PRIMARY KEY,
    league_id     integer NOT NULL,
    season        integer NOT NULL,
    as_of         date NOT NULL,
    team_id       integer NOT NULL,
    player_id     text NOT NULL,
    position      text,
    slot_type     text NOT NULL CHECK (slot_type IN ('starter','bench','ir')),
    source        text NOT NULL,
    ts_precision  text NOT NULL
                  CHECK (ts_precision IN ('exact','date','unknown')),
    recorded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_league_rosters_asof
    ON public.league_rosters (league_id, season, as_of);
-- A roster row is keyed by (league, season, as_of, team, player, source): a
-- re-capture of the same snapshot updates in place, and a partial capture
-- (one screenshot per team) accumulates because the player_ids differ.
CREATE UNIQUE INDEX IF NOT EXISTS uq_league_rosters_row
    ON public.league_rosters (league_id, season, as_of, team_id, player_id, source);

CREATE TABLE IF NOT EXISTS public.market_trends (
    market_trend_id bigserial PRIMARY KEY,
    snapshot_id     bigint NOT NULL REFERENCES raw.sleeper_trending(snapshot_id),
    archive_date    date NOT NULL,
    trend_type      text NOT NULL CHECK (trend_type IN ('add','drop')),
    player_id       text NOT NULL,
    count           integer NOT NULL,
    player_name     text,
    position        text,
    team            text,
    normalized_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_market_trends_day
    ON public.market_trends (archive_date, trend_type);

CREATE TABLE IF NOT EXISTS public.waiver_move_events (
    event_id    bigserial PRIMARY KEY,
    league_id   integer NOT NULL,
    season      integer NOT NULL,
    week        integer,
    team_id     integer NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('add','drop','add_drop')),
    occurred_at timestamptz NOT NULL,
    source      text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_waiver_move_events_team
    ON public.waiver_move_events (league_id, season, team_id);

CREATE TABLE IF NOT EXISTS public.push_log (
    push_id     bigserial PRIMARY KEY,
    event_class text NOT NULL,
    title       text NOT NULL,
    body        text,
    status      text NOT NULL CHECK (status IN ('sent','suppressed')),
    week        integer,
    sent_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_push_log_week ON public.push_log (sent_at);
