# QB VORP Recalibration — Design

**Date:** 2026-07-14
**Branch:** `qb-vorp-recalibration`
**Status:** design (pre-implementation, review-incorporated)
**Gate:** this is an ADR Domain 7 (strategy/valuation) change — the D7 gate enforces the *discipline* of the change (see §4 for its actual, narrower role).

## Problem

The valuation's QB replacement baseline is set too deep, inflating every QB's VORP and driving the draft strategy to over-value (and mis-time) quarterbacks.

VORP = a player's projected points minus a *replacement* player's points (the best freely-available player at that position). Replacement rank is computed from league shape in `ffi/valuation/baseline.py`:

```
QB demand = teams * QB_starters (2) + qb_extra_rostered
```

The default scenario `qb_hoard_12` sets `qb_extra_rostered = 12`, so QB replacement rank = 24 + 12 = **36**. With ~32 NFL starting QBs, the 36th QB is a benchwarmer projecting near-zero — so every startable QB gets a 400–530 VORP (Dak 527), dwarfing elite skill players (Gibbs 210). Only QB carries this padding; RB/WR/TE/K/DEF baselines are anchored at the principled "first non-starter" rank (starter demand + flex).

## Evidence

Two artifacts (`scripts/draft_diagnostic.py`, `scripts/qb_vorp_sweep.py`):

1. **Diagnostic, backtest 2024:** our seat drafted QB-QB-QB in rounds 1–3 (projected VORP 476/387/384), finished **rank 6/12 on actual points** despite the *highest projected roster VORP of all 12 teams*. Trevor Lawrence: projected 384 → actual 224. Skill players taken at "value" (Chase VORP 160 → actual 407) massively outscored. Projected VORP nearly anti-correlated with actual finish.

2. **Replacement-rank sweep** (3 seasons × 30 seeds, actual nflverse points, QB deadlines OFF to isolate the effect):

   | Scenario | QB replacement rank | Actual all-play % (±2SE) |
   |---|---|---|
   | hoard_0 | 24 (starters only) | **60.7% ± 3.6%** |
   | hoard_12 (current) | 36 | 52.2% ± 3.0% |
   | hoard_24 | 48 | 52.2% ± 3.0% |

   Non-overlapping CIs. Mechanism: inflated QB VORP forces QB1 in round 1 *every* draft; the sane baseline delays QB and banks skill value first. hoard_12 ≡ hoard_24 (inflation saturates); the only lever that matters is pulling the rank *in* toward 24. Sanity gate: recompute at rank 36 reproduced stored VORP exactly (max diff 0.000). The sweep's win partly comes from *unrealistically extreme* QB delay (round 13, deadlines off), so it proves direction and magnitude but not the effect under realistic play — hence the realistic re-test below.

## Goal

Find the QB replacement rank (between 24 and 36) that maximizes actual-points performance **without leaving us QB-thin**, under realistic draft rules, and re-baseline the D7 reference.

## Non-goals

- Any change to projections (Layer 1). Situational factors (RB committees, target share) are priced into the projection feed and were already studied (`team-change-residuals`: priced — do not build). Strictly a Layer-2 baseline fix.
- Any change to other positions' baselines (RB/WR/TE/K/DEF already anchored sanely).
- Risk-adjusting QB projections for bust rate (the "fancier" fallback) — only revisited if the search or gate rejects the simple rank change.

## Design

### 1. Guarded rank search (`scripts/qb_vorp_sweep.py`, upgraded)

- **Ranks:** 24, 27, 30, 33, 36 — coarse on purpose (searching every integer over-tunes to three seasons).
- **Realistic rules:** the *tuned* strategy — `qb_by_round=(2,5,9)` (QB deadlines ON) **and `qb_tier_targets=(1,2,99)`** (the throwaway sweep set neither). Answers "under how we'd really draft, which baseline wins and stays safe?"
- **In-memory recompute is faithful (corrected 2026-07-14; supersedes the earlier "materialization prerequisite").** `qb_tier_targets` depends on tiers, but **tiers are rank-invariant**: production tiers cluster on *projected points* (`build_valuation.py:132`, `gmm_tiers([pts …])`), and the replacement rank only shifts the per-position baseline (a constant on VORP), never `pts`. Empirically confirmed: production's own QB tiers are **identical** across `qb_hoard_0` (rank 24) and `qb_hoard_12` (rank 36) — 0/249 mismatches — while VORP differs by ~395; and `gmm(pts) == gmm(vorp)` (GMM is shift-invariant). So the search **recomputes VORP only, in-memory, and keeps the stored (rank-invariant) tiers** — no intermediate scenario needs pre-materializing. Bonus: deriving every candidate rank from one backtest pool's projections **eliminates R6 (vintage mismatch) for the search** (one pool, one vintage). A tier-invariance assertion (regmm on a candidate rank's VORP reproduces the stored tiers) guards the property at run time.
- **Data:** all 3 backtest seasons (2023–25), **50 seeds** each for the search, **100 seeds** on the winner for final confirmation (50 → ±~1.7–2% CI, enough to resolve the ~2–3pt inter-rank gaps; 100 → ±~1.2% for the winner).
- **Metrics per rank:**
  - Actual-points all-play % (the win metric).
  - **QB-depth guardrail:** avg QBs drafted; % of drafts ending with a real QB3.
  - **Injury-robustness guardrail:** after each draft, remove our QB1 and re-grade on actual points; a roster that collapses without its top QB is too thin.
- **Output:** a tradeoff table (win% *and* both guardrails per rank).

### 2. Hard success criterion (the QB3 protection)

Accept a lower baseline **only if it keeps us as QB-safe as the current default** — QB count and injury-robustness at the chosen rank must be no worse than at rank 36. A rank that wins on points but fails a depth guardrail is disqualified. "Don't skip the QB3 we need when QBs are scarce" is a hard gate, not an afterthought.

### 3. The change — switch the default pointer, do NOT mutate `qb_hoard_12`

The winning rank corresponds to a scenario; we **point the default at that scenario** rather than mutating `qb_hoard_12`'s value. Mutating would make the name a lie (`qb_hoard_12` holding `qb_extra_rostered≠12`) and break its value-assertion tests. If the winner is rank 24, the target is the *already-materialized* `qb_hoard_0`; for an intermediate rank, add an honestly-named scenario (e.g. `qb_hoard_6`) and materialize it. `qb_hoard_12` keeps its identity and its value tests survive.

**Full propagation list — every place the default `"qb_hoard_12"` is pinned:**
1. `ffi/sim/strategy.py` — `StrategyParams.scenario` default (line ~104).
2. `ffi/sim/backtest.py` — `VORP_SCENARIO` (a *separate hardcoded dict*, not derived from `SCENARIOS`; must be updated independently).
3. `scripts/run_sim_farm.py` — `SCENARIOS_MAIN = ["qb_hoard_12"]`.
4. `scripts/build_backtest_pools.py` — hardcoded `build_pool(conn, "qb_hoard_12")` (~line 21).
5. `scripts/draft_assistant.py` — the valuation-snapshot scenario it reads.
6. `scripts/build_valuation.py` — K-count validation query (~line 172) and top-25 print (~line 182) hardcode `qb_hoard_12` (display/validation; update if we want them to track the default).

Tests: any test asserting `qb_hoard_12` is the *default* must update. Tests asserting `qb_hoard_12`'s *values* (e.g. `r12["QB"] == 36` in `test_valuation.py`) **survive** under a pointer switch because `qb_hoard_12` is unchanged — a strict advantage over mutate-in-place. New default-scenario values will need their own assertions.

### 4. D7 gate — role, and the re-baseline sequence

**The gate is NOT the arbiter of whether the new rank is good** (spec v1 wrongly called it "final arbiter"). `evaluate_gate` raises on composite-outside-band of a *stored reference*; an intentional valuation change is *supposed* to move the composite (the 4 REF_STRATEGIES no longer collapse to identical QB-first drafts once QBs stop dominating VORP, so behavior — not just labels — changes). So against the stale rank-36 reference the gate is either a false alarm or, after re-baselining, a rubber stamp. **The guarded search (§1–2) is the real arbiter.** The gate's actual jobs: (a) enforce the D7 discipline — no unmeasured valuation ships; (b) re-establish the reference for *future* regression detection; (c) confirm the pipeline still computes.

**Re-baseline sequence (per the Phase 4 Task 4 Step 7 rebuild-after-calibration protocol):**
1. Update the default pointer at all §3 propagation points (incl. `backtest.py VORP_SCENARIO`).
2. Ensure the target scenario's valuation is materialized (`build_valuation.py`); rebuild backtest pools (`build_backtest_pools.py`).
3. `run_backtests.py --gate` — informational here (it will report movement vs the stale reference; that movement is expected, not a failure of the change).
4. **`run_backtests.py --reference`** — establish the new composite as the reference. **Without this, all future gate runs compare against the stale rank-36 reference.** This is the step spec v1 omitted.

Spot-check sample drafts at the chosen rank with `draft_diagnostic.py --backtest <season>`.

## Testing

- The upgraded guarded search + guardrail metrics (decides the rank).
- Test updates: default-assertion tests repointed; new default-scenario value assertions added; `qb_hoard_12` value tests left intact.
- The D7 re-baseline sequence (§4) — pipeline recomputes, new reference established.
- `draft_diagnostic.py --backtest` for qualitative eyeballing at the chosen rank.

## Risks

- **Over-tuning to 3 seasons** — mitigated by the coarse rank grid, the structural (non-season-specific) depth guardrail, and 100-seed winner confirmation.
- **Overshoot into QB-thinness** — mitigated by the §2 hard success criterion.
- **Propagation/pointer drift** — the default is pinned in 6 places (§3) incl. a *separate* hardcoded `VORP_SCENARIO`; missing one silently splits the valuation between paths. Mitigated by the explicit list and the gate refusing on ADP/valuation snapshot mismatch.
- **Stale reference** — mitigated by making the `--reference` re-baseline an explicit §4 step.

## Resolved (were open questions)

- **Seed count:** 50 for the search, 100 to confirm the winner.
- **Naming:** switch the default pointer to the winning scenario (never mutate `qb_hoard_12`); the winner is `qb_hoard_0` if rank 24, else a new honestly-named scenario.
