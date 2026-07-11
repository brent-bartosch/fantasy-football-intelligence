-- 008_signals_pct_cap_fix.sql — Task 15: fix adjustments_pct_check float boundary bug.
--
-- 007_signals.sql's inline CHECK (pct >= -0.10 AND pct <= 0.10) on a `real`
-- column got Postgres's implicit cast wrong: the bare numeric literals widen
-- to `double precision` (`pct <= 0.10::double precision`), but `pct` itself
-- is `real` (float4). float4's nearest representable value to 0.10 is
-- 0.100000001490116119384765625 -- slightly ABOVE the double-precision 0.10
-- literal -- so `pct <= 0.10::double precision` is FALSE for pct == 0.10
-- exactly. The design caps are inclusive (<=10%/<=20%), so the boundary
-- value itself must be insertable. Fix: compare against `::real` casts so
-- both sides round to the same float4 value.
ALTER TABLE signals.adjustments DROP CONSTRAINT IF EXISTS adjustments_pct_check;
ALTER TABLE signals.adjustments
    ADD CONSTRAINT adjustments_pct_check CHECK (pct >= (-0.10)::real AND pct <= (0.10)::real);
