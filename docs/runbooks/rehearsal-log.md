# Rehearsal drill log (ADR D7 evidence)

Each row is one drill run against the fake transport (a historical
NAJEE-season replay). The four written pass criteria:

1. Poll lag p95 < 15s (pick-visible-to-applied).
2. Token refresh mid-session without pick loss.
3. Forced-999 -> MANUAL switchover < 30s (human-timed; the headless
   run measures the machine side: injection -> MANUAL banner).
4. Crash -> resume with full state (derived state exactly equal to a
   never-crashed control).

| date | drill | result | metrics | git sha |
|---|---|---|---|---|
| 2026-07-11 | 999 | PASS | 999@pick40 -> mode=MANUAL after 36 picks; mode_events=1 (MANUAL x1); switchover is immediate (0 retries) | 4fd2b58 |
| 2026-07-11 | refresh | PASS | applied 228/228 picks; token refreshed 1x proactively (margin 900s); 0 missed | 4fd2b58 |
| 2026-07-11 | crash | PASS | crashed@101, resumed to 228/228; taken== counts== overall==(228) mode==(LIVE) | 4fd2b58 |
| 2026-07-11 | lag | PASS | p95=3.52s median=1.07s max=3.57s n=41 (interval=5.0s, cadence=2.5s) | 4fd2b58 |
