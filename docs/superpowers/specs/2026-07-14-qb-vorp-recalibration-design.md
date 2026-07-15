# QB VORP Recalibration — Design

**Date:** 2026-07-14
**Branch:** `qb-vorp-recalibration`
**Status:** design (pre-implementation)
**Gate:** this is an ADR Domain 7 (strategy/valuation) change — the D7 backtest gate is the final arbiter.

## Problem

The valuation's QB replacement baseline is set too deep, inflating every QB's VORP and driving the draft strategy to over-value (and mis-time) quarterbacks.

VORP for a player = his projected points minus a *replacement* player's points (the best freely-available player at that position). Replacement rank is computed from league shape in `ffi/valuation/baseline.py`:

```
QB demand = teams * QB_starters (2) + qb_extra_rostered
```

The default scenario `qb_hoard_12` sets `qb_extra_rostered = 12`, so QB replacement rank = 24 + 12 = **36**. In a league with ~32 NFL starting QBs, the 36th QB is a benchwarmer projecting near-zero — so every startable QB gets a 400–530 VORP (e.g. Dak 527), dwarfing elite skill players (Gibbs 210). Only QB carries this padding; RB/WR/TE/K/DEF baselines are anchored at the principled "first non-starter" rank.

## Evidence

Two artifacts built during investigation (`scripts/draft_diagnostic.py`, `scripts/qb_vorp_sweep.py`):

1. **Diagnostic, backtest 2024 (`draft_diagnostic.py --backtest 2024`):** our seat drafted QB-QB-QB in rounds 1–3 (projected VORP 476/387/384), finished **rank 6/12 on actual points** despite the *highest projected roster VORP of all 12 teams*. Trevor Lawrence: projected 384 → actual 224. Skill players taken at "value" (Chase VORP 160 → actual 407) massively outscored. Projected VORP was nearly anti-correlated with actual finish.

2. **Replacement-rank sweep (`qb_vorp_sweep.py`, 3 seasons × 30 seeds, graded on actual nflverse points, QB deadlines OFF to isolate the effect):**

   | Scenario | QB replacement rank | Actual all-play % (±2SE) |
   |---|---|---|
   | hoard_0 | 24 (starters only) | **60.7% ± 3.6%** |
   | hoard_12 (current) | 36 | 52.2% ± 3.0% |
   | hoard_24 | 48 | 52.2% ± 3.0% |

   Non-overlapping CIs. Mechanism: inflated QB VORP forces QB1 in round 1 *every* draft; the sane baseline delays QB and banks skill value first. hoard_12 ≡ hoard_24 (inflation saturates); the only lever that matters is pulling the rank *in* toward 24. Sanity gate: recompute at rank 36 reproduced stored VORP exactly (max diff 0.000).

The sweep's win partly comes from *unrealistically extreme* QB delay (round 13 in 2024, deadlines off), so it proves direction and magnitude but not the effect under realistic play — hence the design below re-tests under real rules.

## Goal

Find the QB replacement rank (between 24 and 36) that maximizes actual-points performance **without leaving us QB-thin**, under realistic draft rules, and validate it through the D7 gate.

## Non-goals

- Any change to projections (Layer 1). Situational factors (RB committees, target share) are priced into the projection feed and were already studied (`team-change-residuals`: priced — do not build). This is strictly a Layer-2 baseline fix.
- Any change to other positions' baselines (RB/WR/TE/K/DEF are already anchored sanely).
- Risk-adjusting QB projections for bust rate (the "fancier" fallback) — only revisited if the gate rejects the simple rank change.

## Design

### 1. Guarded rank search (`scripts/qb_vorp_sweep.py`, upgraded)

- **Ranks:** 24, 27, 30, 33, 36 — coarse on purpose (searching every integer over-tunes to three seasons).
- **Realistic rules:** run the *tuned* strategy (QB deadlines ON — `qb_by_round=(2,5,9)`, `qb_tier_targets=(1,2,99)`), NOT the deadlines-off isolation used to prove the effect. Answers "under how we'd really draft, which baseline wins and stays safe?"
- **Data:** all 3 backtest seasons (2023–25), ~50–100 seeds each for tight CIs.
- **Metrics per rank:**
  - Actual-points all-play % (the win metric).
  - **QB-depth guardrail:** avg QBs drafted; % of drafts ending with a real QB3.
  - **Injury-robustness guardrail:** after each draft, remove our QB1 and re-grade on actual points; a roster that collapses without its top QB is too thin.
- **Output:** a tradeoff table (win% *and* both guardrails per rank).

### 2. Hard success criterion (the QB3 protection)

Accept a lower baseline **only if it keeps us as QB-safe as the current default** — QB count and injury-robustness at the chosen rank must be no worse than at rank 36. A rank that wins on points but fails a depth guardrail is disqualified. This makes "don't skip the QB3 we need when QBs are scarce" a hard gate, not an afterthought.

### 3. The change

Whichever rank wins is a **one-parameter edit**: the default scenario's `qb_extra_rostered` (12 → chosen value) in `scripts/build_valuation.py`'s `SCENARIOS`, propagated wherever `qb_hoard_12` is the pinned default (`run_sim_farm.py`, `backtest.py VORP_SCENARIO`, the draft-assistant valuation snapshot). Likely the only code change; possibly a new scenario name (e.g. `qb_hoard_N`) if we keep the old for comparison.

### 4. D7 gate validation

The winning rank runs the **full D7 gate** (`REF_STRATEGIES` × `BACKTEST_SEASONS`, composite recompute vs stored reference). It must pass. A new valuation requires rebuilding the backtest pools under the new rank (`build_backtest_pools.py`) and re-running `run_backtests.py`. Spot-check sample drafts at the chosen rank with `draft_diagnostic.py --backtest`.

## Testing

- The upgraded sweep + guardrail metrics (the search itself).
- The D7 gate (the formal referee; composite must hold).
- `draft_diagnostic.py --backtest <season>` for qualitative eyeballing at the chosen rank.

## Risks

- **Over-tuning to 3 seasons** — mitigated by the coarse rank grid, gate validation, and the structural (non-season-specific) depth guardrail.
- **Overshoot into QB-thinness** — mitigated by the hard success criterion (§2).
- **Snapshot/propagation drift** — the default scenario is referenced in several places; the change must update all of them, verified by the gate refusing on mismatch.

## Open questions for the plan

- Exact seed count for the search (50 vs 100) — pick for CI width < the inter-rank gap.
- Whether to introduce a new scenario name or mutate `qb_hoard_12` in place (affects reproducibility of prior reports).
