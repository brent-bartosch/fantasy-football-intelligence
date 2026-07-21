# Spec: `value = VORP × P(starts)` — Phase A prototype

**Date:** 2026-07-20 · **Status:** approved, building Phase A (light exploration)

## Problem
The strategy scores rule-4 candidates on raw `vorp`, which is blind to
`P(a player ever starts)`. It over-values bench depth at single-start positions
(QB3, TE3) and under-weights the leverage of multi-start positions (WR×3). Today
this is patched by hand-tuned `caps` + `qb_not_before` in `DEPLOYED_PARAMS`. The
principled fix is to weight value by the probability the *depth slot* starts.

## Core idea
`P(starts)` is a property of the **depth slot**, not the player:
```
effective_value(candidate) = vorp × P_start[position][slot]
    slot = counts[position] + 1   # this candidate would be my Nth at the position
```
`P_start[pos][slot]` = expected fraction of regular-season weeks that a roster's
`slot`-th-best player at `pos` (by projection) appears in the optimal weekly
lineup (starters + FLEX), given byes.

Single-start positions (QB 2 / TE 1) crater after "starters + 1 insurance";
multi-start positions (RB 2+FLEX / WR 3+FLEX) hold value deeper. This makes
`caps` and `qb_not_before` **emergent**.

## Approach (Monte-Carlo, reuse the season sim)
1. **Estimator** (`scripts/estimate_p_starts.py`): build a representative full
   roster from the live pool (top-by-projection: e.g. 4 QB / 7 RB / 8 WR / 3 TE /
   1 K / 1 DEF). Draw `_mc_weekly_points` (Gamma weekly points + one bye/player,
   the existing model). For each week/season compute the optimal lineup and record
   which players start (a start-mask variant of `_lineup_total`'s top-N-per-pos +
   best-FLEX logic). `P_start[pos][slot]` = start-frequency of the slot-th player.
   - Phase A models **byes only** (matches the current sim). Injuries would lift
     multi-start depth (RB4/WR4) and are a noted fast-follow, not in v1.
2. **Prototype score**: a strategy variant where rule-4 score =
   `vorp × P_start[pos][slot]` (no caps / no `qb_not_before` needed).
3. **Head-to-head backtest**: the P(starts) strategy vs the deployed cap-patch
   (`DEPLOYED_PARAMS`) on ACTUAL points (existing backtest pools), reporting
   playoff-make % / all-play. Decide from data whether it beats the caps.

## Out of scope for Phase A
- Deploying to the live board / D7 gate (that's Phase B, only if A wins).
- Injury model, per-player (not per-slot) P(starts), opponent-model changes.

## Success criterion
A believable `P_start` table (QB3 ≈ 0.15, WR holds deep, TE2 ≈ 0.25) **and** a
head-to-head backtest number vs `DEPLOYED_PARAMS`. Win → Phase B. No win →
documented negative result; the cap-patch stays.
