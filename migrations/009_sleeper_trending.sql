-- 009_sleeper_trending.sql — ADR Precondition P2 (R8).
--
-- Sleeper's trending endpoint is a rolling 24h window with NO historical
-- access: a day not archived is a day of validation data that can never be
-- recovered. Rows are immutable dated snapshots — one row per
-- (archive_date, trend_type) on a normal day.
--
-- Deliberately NOT UNIQUE on (archive_date, trend_type): a retry after a
-- partially-failed run must be able to land, and silently overwriting a
-- day's snapshot would destroy the very thing this table exists to
-- preserve. Continuity is measured with COUNT(DISTINCT archive_date)
-- (scripts/morning_briefing.py), which is insensitive to duplicates.
--
-- The corollary for readers: a point read of "the" snapshot for a day must
-- disambiguate a doubled day by taking MAX(snapshot_id) per
-- (archive_date, trend_type) — the highest id is the last successful write
-- for that day, and it wins. Reading without that aggregation can silently
-- return the partial snapshot a retry superseded.
CREATE TABLE IF NOT EXISTS raw.sleeper_trending (
    snapshot_id    bigserial PRIMARY KEY,
    run_id         integer REFERENCES raw.ingest_runs(run_id),
    archive_date   date NOT NULL,
    trend_type     text NOT NULL CHECK (trend_type IN ('add','drop')),
    lookback_hours integer NOT NULL CHECK (lookback_hours > 0),
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    payload        jsonb NOT NULL          -- full API response, untouched
);
CREATE INDEX IF NOT EXISTS idx_sleeper_trending_day
    ON raw.sleeper_trending (archive_date DESC, trend_type);
