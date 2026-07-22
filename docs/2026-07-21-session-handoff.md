# Session Handoff — 2026-07-21

## TL;DR
**Starts-weighted valuation v2 is DEPLOYED** (`9b3fd19`). In one day, item 4 went
from Phase-A NO-GO (naive `VORP × P(starts)` lost 46.7% vs 75.3% and drafted 10
QBs) → diagnosis (VORP's 2QB-inflated baseline, not the P(starts) idea) →
redesign (starts-based replacement, zero new knobs) → 5-season backtest
extension → causal ablation of a synthetic-projection bias → fresh-seed strict
win → Phase-B wiring with a green D7 gate and byte-identical live-path
reproduction. All local; **NOT pushed** (user was away; push when they say so).

## The deployed model
- **Starts-based replacement:** `R_pos = round(12 × Σ_slot P_start[pos][slot])`
  → **QB24 / RB36 / WR36 / TE12** (was roster-based ~QB36). Baked into `vorp`
  in `build_valuation` AND `build_season_pool`. Canonical table now TRACKED at
  `data/p_starts.json` (byes+injuries, seed 7, `_meta` enforced fail-loud).
- **Deployed score (A′):** `P_start[pos][k+1] × vorp` (equals
  `P_start × (proj − points_at_rank(pos, R_pos))` by construction). Opt-in via
  `StrategyParams.pstart_weights`; `DEPLOYED_PARAMS` sets it; REF_STRATEGIES /
  bare `StrategyParams()` keep legacy scoring (D7 reference intact).
- **Kept as rules (honesty):** `qb_by_round=(2,5,14)` timing force — emergent QB
  timing provably loses to a QB run in a 2QB room (traced: QB1's value over
  QB24-replacement ≈145 < RB1's 207, and calibrated opponents drain elite QBs
  by R2-3). TE≤2 insurance cap, `defk_round`, feasibility. **v2 replaces the
  VALUATION; QB timing stays a guardrail** (user's instinct confirmed).
- Effect on the board: QB VORP deflated exactly as designed (Allen 518.5→205.8);
  2 QBs in the top-15 instead of a QB-dominated top; RB +31, WR −9.

## The evidence chain (all numbers = H2H playoff-make % on ACTUAL points)
1. **Round 1 (100 drafts/season, 5 seasons):** DEPLOYED 79.4 ±3.6, A′ 78.8
   ±3.7, B 76.2 ±3.8 — composite NO-GO, but A′ beat DEPLOYED on every
   real-projection season (2022 91v85, 2023 72v53, 2025 88v89) and lost only on
   synthetic ones (2021, 2024).
2. **Pre-registered hypothesis + causal ablation (`12db46a`, `fbf320a`):**
   synthetic seasons graft the 2026 points curve onto old ECR order → fake
   cross-position gaps = the gap-based engine's direct input; caps are ordinal
   and insensitive. Test: 2023 re-run with real projections swapped for the
   synthetic curve, same actuals/seeds, 400 paired drafts. A′'s edge +17.2 →
   +6.8; paired difference-of-differences **+10.5 ±7.3, CI [+3.2, +17.8]
   excludes 0. CONFIRMED** → defensible primary = real-projection seasons
   (2022/2023/2025).
3. **B bugfix:** replacement level was only applied to literally-empty slots, so
   sub-replacement bench adds produced impossible negative marginals and B
   bought TEs as "least-negative" filler. Fixed (replacement floors every
   starter slot + FLEX). Post-fix B still takes TE3 by a small POSITIVE
   marginal (~0.4-1.7) — the model honestly disagreeing with the TE≤2
   heuristic, left uncapped in B, documented open question.
4. **Confirmation (FRESH disjoint seeds, 300 drafts/season, primary n=900):**
   **A′ 85.3 ±2.4 [83.0, 87.7] vs DEPLOYED 76.8 ±2.8 [74.0, 79.6] —
   NON-OVERLAPPING; paired diff +8.6 [+5.2, +11.9].** B 82.8 [80.3, 85.3] clears
   independently (convergent evidence). 5-season supplement washes out to +1.7
   [−1.0, +4.4] exactly as the bias hypothesis predicts.
5. **Phase B (`9b3fd19`):** D7 gate **PASSED 0.5652 ≥ 0.5101** (pre-wiring
   0.5310 — starts-based valuation helps even the reference cap strategies).
   Live `DEPLOYED_PARAMS` path reproduces the prototype **byte-identically
   (900/900 seeds)**. 470 tests pass. Consumers verified: demo, pick_advisor
   (pick #1 Bijan 221 = 0.763×289), cheat_sheet_html regenerated,
   draft_assistant imports. Live 2026 sanity board: QB R2/R5/R14, TE2, 1K/1DEF,
   RB-heavy 7/5.

## Backtest infrastructure changes (`893328d`)
- **Seasons extended 2023-25 → 2021-25.** 2021/2022 superflex ECR from
  dynastyprocess `db_fpecr.parquet` (Aug snapshots, 0-1d off); Wayback
  recovered REAL projections beyond spec: 2022 ≈ fully real QB/RB/WR/TE, 2021
  real QB+RB. Match gate 99.2%/99.6%; one override (Zonovan/Bam Knight).
- **`GATE_SEASONS=(2023,2024,2025)` frozen** — D7 gate definition unchanged
  while `BACKTEST_SEASONS` grew. `build_backtest_pools.py --seasons` added.
- Caveat to carry: 2021 RB projections are a Week-2 (2021-09-19) snapshot —
  mild cross-position scale wobble in 2021 numbers.

## Open items (priority order)
1. ~~Push to origin~~ **DONE 2026-07-21** (`ec46ddf..aaf362a` pushed on user
   direction).
2. **Sim farm restart** (dark since 07-12) — now must ALSO pick up the new
   valuation; point at DEPLOYED_PARAMS + playoff-make %, then `launchctl load`.
3. ~~D7 reference re-establish~~ **DONE 2026-07-21** (user direction):
   `--reference` stored composite **0.5652 band 0.0290**; gate re-verified
   PASSED against the new threshold 0.5361. Prior row (0.52969) deactivated,
   not deleted.
4. ~~Trade-market lead~~ **CLOSED 2026-07-21:** user directed ignore + delete;
   the qb-monopoly files (other session's untracked experiment) were deleted
   without review. If post-draft trading ever resurfaces as a question, treat
   it as fresh work — no adopted numbers from that exercise.
5. **B's TE3 disagreement** — marginal model says a 3rd TE behind an elite TE1
   is worth ~0.4-1.7 pts over a 9th WR's 0.0. Cheap test someday; A′/TE≤2 is
   what's deployed and it won.
6. Floor-aware grade metric; VONA into the pick engine (B's marginal is the
   natural host); QB-timing opponent-sensitivity check (lowest, see 07-20
   handoff item 4).

## Reconciliation with 2026-07-15 "QB VORP baseline = dead knob"
Both true, no contradiction: the 07-15 sweep varied replacement rank UNDER the
caps/timing strategy, whose QB picks are ordinal within position → rank shift
changed nothing. Today's change matters because A′ consumes the cross-position
GAP (P_start × vorp); the baseline is dead as a knob for the old strategy and
load-bearing for the new score.

## Process notes
- Spec + pre-registration: `docs/superpowers/specs/2026-07-21-starts-weighted-valuation-v2-design.md`.
- Results JSONs on disk (reports/ gitignored): `tournament-v2-2026-07-21*.json`,
  `ablation-2023-2026-07-21.json`, `tournament-v2-confirmation-2026-07-21.json`.
- User directive 2026-07-21: check-in gates removed for this run; evidence
  gates (strict CI win + D7) were kept and met. All implementation by Opus
  agents (opus-seasons `893328d`; opus-engines `9f2f40d`, `fbf320a`, `9b3fd19`).
