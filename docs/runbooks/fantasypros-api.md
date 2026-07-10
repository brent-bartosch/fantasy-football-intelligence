# FantasyPros Public API — key application & usage policy

## Apply (user action, do this on day 1 — approval is discretionary and takes unknown time, risk R12)
1. Log into your FantasyPros account in a browser.
2. Go to https://secure.fantasypros.com/api-keys/request/
3. Describe intended use as: personal, non-commercial fantasy football research for
   your own league; daily cached sync of projections/rankings/ADP; ~20 calls/day.
4. When the key arrives, add to `.env`: `FANTASYPROS_API_KEY=<key>` (never commit).

Status: **key approved and verified live 2026-07-09** (HTTP 200 on 2026 consensus-rankings; key in `.env` as `FANTASYPROS_API_KEY`, never committed). Call budget below is now in effect.

## Hard limits (ToS, verified 2026-07-08)
- 1 call/second, 100 calls/day. Personal, non-commercial. No redistribution.
- Historical player statistics are explicitly NOT licensed — never store them from FP
  (we use nflverse for historicals anyway).

## Call budget (ADR Domain 6)
- One daily sync ≤ 30 calls: projections (QB/RB/WR/TE/K/DST × draft), consensus-rankings
  (superflex + positional), ADP. Everything else reads the local cache in `raw`.
- Ad-hoc queries NEVER hit the API directly.

## Fallback if key is denied/delayed (R12)
- `ffpros`-style authenticated page parsing / `?export=xls` with session cookie.
- Sleeper remains the projection backbone either way; FP is the consensus overlay.
