-- 010_ingest_run_status.sql — ADR Domain 1: semantic sanity gates need their
-- own run statuses, distinct from 'failed'.
--
--   sanity_failed  the gate raised and the ingester refused to store
--                  (hard-fail mode: sleeper_projections' END state, from
--                  2026-09-08 — see the soak note below)
--   sanity_warned  the gate raised, the payload WAS stored, and the run is
--                  flagged (observe-and-log mode: sleeper_trending — the
--                  archive is unrecoverable, so refusing to store a suspect
--                  day would destroy more than it protects)
--
-- Per-feed numeric thresholds beyond the >=0.85 rank correlation are unset
-- (ADR TBD 3) and will be fitted from the first four weeks of the P2
-- archive. Until then NO feed hard-fails: trending is observe-and-log
-- permanently (R8 — the archive is unrecoverable), and projections is in a
-- warn SOAK until 2026-09-08, after which its end state is 'fail' (nothing
-- stored, run recorded 'sanity_failed'). So 'sanity_failed' rows are
-- expected only from an explicit sanity_mode='fail' override
-- (scripts/ingest_sleeper.py --sanity-mode fail) until that flip.
-- The soak's measurement lives in sanity_rho (migration 011), written on
-- passing runs too — `error` alone only ever recorded the failures.
ALTER TABLE raw.ingest_runs DROP CONSTRAINT IF EXISTS ingest_runs_status_check;
ALTER TABLE raw.ingest_runs
    ADD CONSTRAINT ingest_runs_status_check
    CHECK (status IN ('running','success','failed','sanity_warned','sanity_failed'));
