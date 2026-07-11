# Rehearsal drill log (ADR D7 evidence)

Each row is one drill run against the fake transport (a historical
NAJEE-season replay). The four written pass criteria:

1. Poll lag p95 < 15s (pick-visible-to-applied).
2. Token refresh mid-session without pick loss.
3. Forced-999 -> MANUAL switchover < 30s (human-timed; the headless
   run measures the machine side: injection -> MANUAL banner).
4. Crash -> resume with full state (derived state exactly equal to a
   never-crashed control).

The poll interval is validated at the ADR band **ceiling (10s)** -- the binding
worst case for the lag criterion, where the interval dominates end-to-end lag.
The first block below ran at the easy 5s end and is retained as history; the
lag row there is **superseded** by the 10s-ceiling run. Rows are grouped by
run; the git sha is HEAD at run time.

| date | drill | result | metrics | git sha |
|---|---|---|---|---|
| 2026-07-11 | 999 | PASS | 999@pick40 -> mode=MANUAL after 36 picks; mode_events=1 (MANUAL x1); switchover is immediate (0 retries) | 4fd2b58 |
| 2026-07-11 | refresh | PASS | applied 228/228 picks; token refreshed 1x proactively (margin 900s); 0 missed | 4fd2b58 |
| 2026-07-11 | crash | PASS | crashed@101, resumed to 228/228; taken== counts== overall==(228) mode==(LIVE) | 4fd2b58 |
| 2026-07-11 | lag | PASS | p95=3.52s median=1.07s max=3.57s n=41 (interval=5.0s, cadence=2.5s) — SUPERSEDED by the 10s-ceiling run below (5s is the easy end of the ADR band; not binding evidence) | 4fd2b58 |
| 2026-07-11 | 999 | PASS | 999@pick40 -> mode=MANUAL after 31 picks; mode_events=1 (MANUAL x1); switchover is immediate (0 retries) | e66edbc |
| 2026-07-11 | refresh | PASS | applied 228/228 picks; token refreshed 1x proactively (margin 900s); 0 missed | e66edbc |
| 2026-07-11 | crash | PASS | crashed@101, resumed to 228/228; taken== counts== overall==(228) mode==(LIVE) | e66edbc |
| 2026-07-11 | lag | PASS | p95=7.87s median=2.93s max=7.93s n=41 (interval=10.0s [ADR ceiling], cadence=2.5s) | e66edbc |
