# Session Handoff — 2026-07-20

Pick-up doc for a fresh session. Durable state is in auto-memory
(`fantasy-2026-project-state.md`, updated this session); this is "what just
happened + what's next." **The next task is Item 4 Phase A Part 2** (below).

## TL;DR
This session fixed a real valuation bug, closed R8, built a full grading/cheat-
sheet/advisor tool suite, ran a live pick-advised FP mock (finished **3rd/12**),
and **kicked off Item 4 (`value = VORP × P(starts)`)** — Phase A Part 1 (the
P(starts) estimator) is built, validated, and committed. All on `main`, pushed.
**Next up: Item 4 Phase A Part 2 — prototype the score + the go/no-go backtest.**

## What shipped this session (all on `main`)
- **Incompletion-penalty bug FIXED** (`e77cbca`, `sleeper_adapter.py`): league scores
  incompletions −0.5 but Sleeper omits `pass_inc`; we now derive `att − cmp`. Every
  projected QB was over-scored ~80–99 pts. Re-scored → valuation rebuilt → **D7 gate
  PASSED 0.5310**. QBs deflated (Allen 655→575), QB tier reshuffled (Lamar 7th→3rd).
  Projection-only (actuals were always correct) = the mechanism behind the "projected
  flatters QB-hoarding" mirage. **Pipeline to propagate any projection/scoring change:**
  `score_sleeper_projections.py → build_valuation.py → build_backtest_pools.py →
  run_backtests.py --gate` (build_valuation reads pre-scored `scoring.projection_points`,
  NOT the adapter — re-score FIRST).
- **`DEPLOYED_PARAMS`** (`f25df70`, `ffi.sim.strategy`): single source of truth for the
  live strategy (QB3-late + TE-cap-2). Live assistant + `demo_single_draft.py` import it;
  demo `--default` shows old behavior. Fixes the drift where the demo looked like the
  deploy was lost (it wasn't).
- **R8 FULLY CLOSED** (`audit_scoring_settings.py`, `002bbd8`): live 2026 scoring = clean
  PASS vs `league_rules.md` + byte-identical to 2025. Renewal preserved scoring 100%.
- **Tool suite:** `grade_board.py` (grade any external draft + Spearman edge),
  `cheat_sheet.py` (markdown — deprecated, hard to read), `cheat_sheet_html.py`
  (**interactive HTML board** — position columns, tier colors, click/search to cross
  off; this is THE cheat sheet now), `pick_advisor.py` (live mock advisor via the
  deployed engine — but it's myopic, see caveat below), `estimate_p_starts.py` (item 4).
- **Item 4 Phase A Part 1** (`1dc5a36`): P(starts) estimator + spec
  (`docs/superpowers/specs/2026-07-20-p-starts-valuation-design.md`).

## >>> NEXT TASK: Item 4 Phase A Part 2 (prototype + backtest) <<<
**Goal:** decide, on actual points, whether `value = VORP × P(starts)` beats the
hand-tuned `DEPLOYED_PARAMS` caps. This is the go/no-go for Phase B (deploy).

**The P(starts) table (from `estimate_p_starts.py`, byes + injuries), the input:**
```
  slot:     1     2     3     4     5     6
  QB      .83   .83   .26   .07              (single-start craters at QB3)
  RB      .76   .76   .77   .42   .18   .07  (multi-start holds via slots+FLEX+injury)
  WR      .81   .81   .81   .38   .14   .05
  TE      .81   .15   .03                    (single-start craters at TE2)
  K/DEF   .91   .08
```
Table is written to `reports/p_starts-<date>.json`. The estimator is
availability-based (lineups set by projection rank among AVAILABLE players; NOT
weekly scoring noise — the first cut made that mistake and got ~0.5 for every QB).
Injury λ (games/season) are ASSUMPTIONS in `estimate_p_starts.INJURY_LAMBDA`
(QB1.5/RB2.5/WR1.8/TE1.8) — tunable; byes-only understates RB/WR depth.

**Build steps:**
1. **Prototype PickFn**: a strategy variant where rule-4 score =
   `vorp × p_start[pos][slot]`, `slot = counts[pos] + 1`. Load the JSON table.
   Either add a `p_starts` field to `StrategyParams` (and branch `_score`/
   `rule4_candidates` in `strategy.py`), or build a standalone `PickFn` in the
   script. Prefer the standalone script for Phase A (don't touch live `strategy.py`
   until Phase B). Drop `caps`/`qb_not_before` (they should be emergent) — but keep
   the K/DEF `defk_round` force and feasibility rules.
2. **Head-to-head backtest**: run the prototype PickFn vs `DEPLOYED_PARAMS`'s
   PickFn on the backtest pools (2023–25), scored on ACTUAL points. See
   `ffi.sim.backtest` (`REF_STRATEGIES`, `run_all_cells`, `composite_and_band`,
   `season_data_vintage`) and `scripts/run_backtests.py` for how a strategy is
   drafted + graded on actuals. Reuse the same actual-points machinery
   `qb_timing_h2h.py` / `positional_depth.py` used (playoff-make % via H2H is the
   metric we TRUST; all-play flatters). Report both strategies' number + CIs.
3. **Decision:** VORP×P(starts) wins (non-overlapping, on playoff%) → **Phase B**:
   add the knob to the live valuation/strategy, re-run the D7 gate, retire
   `caps`/`qb_not_before`. No win → documented negative result; caps stay.

**Caveats to respect:** backtest is BLIND to RB value (2024 RB/WR/TE projections
synthetic; QB-timing-only differentiation) — so a P(starts) win/loss will show
mostly through QB3/TE handling, less through RB depth. The `qb_hoard_12` valuation
already over-ranks QBs by VORP; P(starts) is exactly the corrective. Keep it LIGHT
(no full risk/ADR cycle for Phase A — user's standing preference).

## Other open items (lower priority)
1. **Fix + restart the nightly sim farm** — dark since 2026-07-12 (`com.ffi.simfarm`
   launchd job unloaded), AND it scores the OLD strategy on all-play (wrong metric).
   Point it at `DEPLOYED_PARAMS` + add playoff-make %, then `launchctl load`.
2. **Balance/floor-aware grade** — `grade_board`/`grade_roster` rank on projected
   optimal-starter TOTAL, which flatters lopsided rosters (user's own insight: 3 WR
   slots vs the weakness there). A floor/H2H-playoff% grade tells the truth.
3. **Fold scarcity/availability into the pick engine** — during the live mock I had to
   OVERRIDE `pick_advisor` on nearly every early pick (raw VORP wanted QBs; real value
   was scarce RB + late QB). Item 4's P(starts) partly fixes this; VONA
   (`ffi.sim.availability`) folded into the score would finish it.

## Carry-forward facts
- **Draft: Saturday Aug 29 2026.** Freeze ~Aug 22. Real league `470.l.152123`; test
  league dead (Yahoo gates 2QB behind premium — user won't pay). Doesn't matter: the
  **live draft auto-polls** (`draft_assistant.py` live mode, hands-off) and the 2025
  `draft_results` payload is validated. FP mocks are NOT Yahoo-API-visible → only the
  HTML cheat sheet / screenshot-grading works for mocks.
- **Mock-2 result (`grade_board.py`):** Spœrts (slot 8) finished **3rd/12** (3525;
  top-3 within 39 pts). Best QB duo + RB pair + FLEX in the field; **WR is the weak
  spot** (worst in the top tier) → #1 in-season/next-draft priority. The chaotic 12-QB
  early run HELPED (got two solid QBs while the field overpaid for one elite).
- `main` pushed to origin (github.com/brent-bartosch/fantasy-football-intelligence).
