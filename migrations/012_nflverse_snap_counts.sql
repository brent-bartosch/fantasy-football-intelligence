-- 012_nflverse_snap_counts.sql — snap share for the usage engine (R27).
--
-- Numbered 012, not 011 as the task brief drafted it: 011 was taken by
-- 011_ingest_run_sanity_rho.sql (Task 6) before this task landed.
--
-- raw.nflverse_player_week carries no snap, route or red-zone columns
-- (verified 2026-08-31), so the trend engine's headline rule
-- (snap_share > 55% for two consecutive weeks) has no input without this
-- table. nflverse publishes offense_pct directly, so no team denominator is
-- derived here and the partial-publish short-denominator failure (R5)
-- cannot apply to this metric.
--
-- Keyed on gsis_id, resolved from pfr_player_id at ingest via
-- nflreadpy.load_players(); a match rate below the ingester's floor is a
-- hard failure, not a quiet row drop.
CREATE TABLE IF NOT EXISTS raw.nflverse_snap_counts (
    gsis_id       text NOT NULL,
    season        integer NOT NULL,
    week          integer NOT NULL,
    team          text,
    position      text,
    offense_snaps real,
    offense_pct   real,      -- 0.0-1.0, nflverse-published; NOT derived here
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gsis_id, season, week)
);
CREATE INDEX IF NOT EXISTS idx_snap_counts_season_week
    ON raw.nflverse_snap_counts (season, week);
