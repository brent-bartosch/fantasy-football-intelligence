-- 013_usage_weekly.sql — the per-player-week usage table (ADR Domain 2).
--
-- Numbered 013, not 012 as the task brief drafted it: 012 was taken by
-- 012_nflverse_snap_counts.sql (Task 7) before this task landed.
--
-- Share metrics are NULLABLE ON PURPOSE (R5): when a team-week is
-- incompletely published, the builder writes NULL and logs, rather than
-- dividing by a short denominator. A short denominator inflates every
-- share on that team and fires a false ASCENDING for the whole backfield.
-- `games_complete` (0/1 for this player's team-week) and `teams_observed`
-- (distinct teams in the slate) make the refusal auditable after the fact.
--
-- route_share and rz_touches are declared here but are ALWAYS NULL in Plan
-- 1: nflverse participation data ends in 2023 and red-zone touches need a
-- pbp feed. Their rules are disabled by the `requires` mechanism in
-- ffi.usage.trends and announced in the briefing — degrade by removal, not
-- by silent null (R27). The columns exist so Plan 2 can fill them without a
-- migration.
--
-- Week coverage: raw.nflverse_player_week runs through the postseason (week
-- 22); raw.nflverse_snap_counts is REG-only (max week 18). Weeks 19+ build
-- fine — the snap LEFT-join simply misses, so snap_share is NULL there while
-- target_share / carry_share still compute. That is expected, not a gap.
CREATE TABLE IF NOT EXISTS public.usage_weekly (
    gsis_id        text NOT NULL,
    season         integer NOT NULL,
    week           integer NOT NULL,
    team           text NOT NULL,
    position       text,
    snap_share     real,
    target_share   real,
    carry_share    real,
    route_share    real,
    rz_touches     integer,
    team_targets   integer NOT NULL,
    team_carries   integer NOT NULL,
    games_complete integer NOT NULL CHECK (games_complete IN (0,1)),
    teams_observed integer NOT NULL,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gsis_id, season, week)
);
CREATE INDEX IF NOT EXISTS idx_usage_weekly_season_week
    ON public.usage_weekly (season, week);
