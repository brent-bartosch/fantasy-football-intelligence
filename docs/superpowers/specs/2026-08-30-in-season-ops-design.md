# In-Season Operations System — Design

**Date:** 2026-08-30
**Status:** Approved-pending-review
**Context:** Draft completed 2026-08-29 (slot 12, 2QB league 461.l.326814). Season kicks off ~Sept 10.
Draft is ~40% of season outcome; this system is the other 60%.

## Goals

1. Waiver-wire awareness: ranked, valuation-backed add/claim recommendations weekly.
2. League drop monitoring: catch valuable drops inside their waiver window; pounce on clears.
3. Player role trending: in-house "ascending/falling" usage report per NFL team, ahead of market.
4. Exploit all five transaction arbitrage windows (below) plus trade-angle generation.

## Constraints & context

- **Yahoo API 403-gated.** Application pending (~1–2 wk per Yahoo; expected ≈ season kickoff).
  Decision: API is the primary league-state source. Until approval, ad-hoc screenshot parsing
  (proven during the live draft) covers the gap. **No web scraping.**
- League mechanics (league_rules.md): rolling-priority waivers, 1-day waiver clear,
  weekly waivers Game Time → Tuesday, max 5 adds/week, 65/season, trades commissioner-reviewed,
  deadline Nov 22, playoffs weeks 15–17 (regular season 1–14).
- Existing infra to reuse: Postgres, nightly sim-farm, 7am morning-briefing launchd job,
  nflverse weekly ingest, Sleeper projections ingest (daily), FP news → signals queue,
  starts-weighted valuation engine v2, golden-fixture test pattern.
- Roster context: QB room (Willis/Shough/Sanders) is the flagged risk in a 2QB format;
  K slot currently empty (ops task, pre-Week 1).

## Approach

**A. Extend-in-place** (chosen): four new modules on the existing script + launchd + Postgres
pattern. Rejected: (B) unified daemon — over-engineered for one user; (C) manual scripts — no
alerting.

## The five arbitrage windows (+ trades)

| # | Window | Mechanism | Our play |
|---|--------|-----------|----------|
| 1 | Tuesday waiver claims | Rolling priority processes Tue | Ranked claims, priority-priced |
| 2 | Post-clear FA sniping | Drop + 1 day → instant-add FA at predictable timestamp | Track clear-times, alert at the minute |
| 3 | Sunday inactives (~90 min pre-kick) | Surprise scratch → backup instant-add until lock | Pre-made contingency cards ("if X out → add Y") |
| 4 | Priority-list hoarding | Priority is a hoardable asset; overreactors burn it early | Price every claim vs league-winner base rate (2–3/season) |
| 5 | Acquisition-budget endgame | Streamers hit 65-move cap by playoffs | Ledger all 12 teams' moves; strike when opponents can't counter |
| 6 | Trade angles | Usage moves before production; production before managers | Buy-low/sell-high candidates from trend engine × opponent needs |

## Components

### 1. Data layer
- `usage_weekly` derived table from existing nflverse ingest: snap share, target share,
  carry share, route participation, red-zone touches per player-week.
- `league_transactions`, `league_rosters` tables. Filled by `ingest_yahoo_transactions.py`,
  an **adapter**: Yahoo API backend when key works; screenshot/HTML paste parser as fallback.
  Same output schema either way.
- `market_trends` table: Sleeper trending add/drop counts (free API, no auth, daily pull).

### 2. Usage-trends engine — `usage_trends.py`
- 3-week rolling deltas on usage metrics for every skill player.
- Role-change detection rules (initial set, tuned via fixtures):
  - snap share crosses 55% two consecutive weeks (ascending) / falls below prior-baseline −40% (falling)
  - RZ touches strictly increasing 3 weeks
  - target share +5pp vs 3-week baseline
  - 2QB special: any depth-chart QB change league-wide (32 teams) is a loud signal
- Output: ASCENDING / FALLING / WATCH lists with evidence lines.
- Sleeper `market_trends` overlay: flags whether the market has noticed yet
  (our-signal-minus-market-signal gap = urgency score).

### 3. Waiver advisor — `waiver_advisor.py`
- Rest-of-season valuation: Sleeper ROS projections × valuation-v2 starts weighting,
  adjusted by trend signals.
- Values every free agent **vs roster cutline** (worst droppable player, position-aware).
- Priority pricing (window 4): claim-now vs wait-for-clear vs skip, priced against the
  league-winner base rate and current priority position.
- Clear-time tracker (window 2): timestamp every drop, emit upcoming FA-clear schedule.
- Contingency cards (window 3): for each rostered/opponent fragile starter, pre-compute
  the "if inactive → add this backup" card for the Tuesday report.
- Budget ledger (window 5): moves-remaining for all 12 teams from transaction history;
  own-pacing guard (keep ≥10 moves for weeks 15–17).
- Enforces 5/week cap in recommendations.
- Standing QB watchlist: any QB entering the pool with a starting path is flagged
  regardless of general ranking (2QB).

### 4. Trade-angle generator — `trade_angles.py` (window 6)
- Opponent needs: each team's positional surplus/deficit vs starting requirements,
  bye-week crunches 2 weeks ahead, move-budget squeeze.
- Buy-low: usage ascending + production lagging (points below expected from role).
- Sell-high: usage falling + name-value intact (market trend/ADP still strong).
- Output: 2–3 concrete angles/week ("Team X starts 0 healthy TEs weeks 9–10; offer
  Strange + flex for their RB3") in the Tuesday report. Recommend-only; no automation.
- v1 = heuristic rankings; deeper market modeling stays in the open trade-market lead.

### 5. Tuesday "Trends & Targets" report
- New launchd job Tue ~6:30am (post-MNF data): `reports/trends-YYYY-WW.md`.
- Sections: Role trends (ASC/FALL/WATCH) · Top-10 waiver targets with claim/wait/skip ·
  This Week's Windows (Thu lock, pending clear-times, Sunday inactive checkpoints,
  contingency cards) · Trade angles · Own-roster bye/injury exposure next 2 weeks.

### 6. Alerts
- Folded into existing 7am briefing (no new infra): valuable-drop-above-cutline banner,
  QB-watchlist hits, clear-time reminders for same-day windows.
- Push notifications deferred until proven necessary (YAGNI).

## Data flow

nflverse weekly ─→ usage_weekly ─→ usage_trends ─┐
Sleeper ROS proj ─→ valuation v2 (ROS mode) ─────┼─→ waiver_advisor ─→ Tuesday report
Sleeper trending ─→ market_trends ───────────────┤            └──────→ briefing alerts
Yahoo API/screenshots ─→ league_transactions/rosters ─→ clear-times, budgets, trade_angles

## Error handling

- Fail-loud per house rules: missing/stale nflverse week → report renders with an explicit
  DEGRADED banner naming the stale source, never silently thin. Yahoo adapter failure →
  league-state sections show "NO DATA since <timestamp>", not empty lists.
- Sleeper API failures retry with backoff; briefing health section already tracks ingest ages.

## Testing

- Golden fixtures (existing pattern): canned nflverse weeks containing a known 2025 backfield
  flip must fire ASCENDING; canned transaction HTML/screenshot text must parse to rows;
  cutline + priority-pricing unit tests against fixture rosters; clear-time math tests
  (drop timestamp + 1 day, tz-aware).
- Backtest hook: run trend rules over 2025 season, measure lead time vs market
  (Sleeper trending) for known role changes.

## Out of scope (v1)

- Yahoo web scraping; DFS-style lineup optimization; auto-sent trade offers;
  push notification infra; deep trade-market equilibrium modeling.

## Build order (suggested for planning)

1. Data layer + usage-trends engine + backtest validation (works today, no Yahoo needed)
2. Tuesday report with trends + market overlay + ROS waiver rankings vs static rosters
3. Yahoo adapter on API approval → transactions, clear-times, budgets, contingency cards
4. Trade-angle generator
