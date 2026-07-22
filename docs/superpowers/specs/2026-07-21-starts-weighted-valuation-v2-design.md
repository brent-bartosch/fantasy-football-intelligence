# Starts-Weighted Valuation v2 — design (2026-07-21)

Item 4 continuation after the Phase A NO-GO (`8f83869`). User directive
2026-07-21: check-in gates removed; iterate autonomously to a good result;
all implementation by Opus agents. Deploy remains gated by EVIDENCE (strict
win rule + D7), just not by user check-ins.

## Why the naive formula failed (context)

`score = VORP × P_start` lost to DEPLOYED caps 46.7% vs 75.3% (H2H
playoff-make %, non-overlapping CIs) and drafted 10 QBs. Mechanism: VORP's
replacement level is roster-based (~QB36 in 2QB), so every QB carries huge
VORP; a multiplicative discount cannot fix a cross-position scale mismatch.
The P(starts) discount itself worked (TE2 suppressed). The fix is the
BASELINE, not the multiplier.

## Core idea: starts-based replacement (no new knobs)

League-wide expected starts at a position = `12 × Σ_slot P_start[pos][slot]`
(byes+injuries table, seed 7, `_meta.mode == "byes+injuries"` enforced):

| pos | Σ P_start | replacement rank |
|-----|-----------|------------------|
| QB  | ~1.98     | **QB24** (was ~QB36) |
| RB  | ~2.99     | **RB36** |
| WR  | ~3.01     | **WR36** |
| TE  | ~0.99     | **TE12** |
| K/DEF | ~0.99   | 12 (unchanged in practice; defk_round owns timing) |

Replacement = the last player who meaningfully STARTS, not the last one
rostered. Falls straight out of the existing table — zero new parameters.

## Two pick engines (both tested; A′ is B's closed-form approximation)

- **A′ (fast):** `score = P_start[pos][k+1] × (proj_points −
  points_at_rank(pos, R_pos))`, `k = counts[pos]`, `R_pos` = starts-based
  replacement rank on THAT season's pool. Keep `defk_round` force +
  feasibility; DEF/K excluded from voluntary rule-4 picks (carried decision).
- **B (full):** marginal expected lineup points: `E[optimal weekly lineup
  pts | roster + X] − E[| roster]`, Monte-Carlo over the same byes+injuries
  availability model, unfilled slots assumed filled at replacement level
  (`points_at_rank(pos, R_pos)`). Common random numbers across candidates at
  the same pick (same seed → same availability draws) so comparisons are
  noise-free. ~300 MC seasons per eval; prune candidates rule4-style
  (top-N per position) for compute.

## Backtest extension: 3 → 5 seasons (2021–2025)

- 2021/2022 superflex ECR: dynastyprocess `db_fpecr.parquet` (weekly FP ECR
  scrapes back to 2019-12, incl. `ppr-superflex-cheatsheets.php`; August
  snapshots). Wayback projection probe optional; expected MISS → designed
  synthetic-curve fallback on real ECR order, `degraded=True` carried.
- Actuals: nflverse `player_week_points` covers 2019+. Slot priors already
  span 16 seasons. CV fit already 2019–25.
- `BACKTEST_SEASONS` extended to (2021..2025); **the D7 gate's definition
  stays 2023–25** (it is a regression baseline; do not move it mid-flight).
- Match gate (≥85% top-150) enforced for new seasons; misses go to
  `data/backtest_name_overrides.json`.

## Tournament protocol

Strategies: DEPLOYED caps, A′, B. Paired seeds, 100 drafts/season each,
scored on ACTUAL points, H2H playoff-make % (the trusted metric; all-play
flatters). Report per-season + 5-season composite ± 2SE + positional counts
+ one pick-by-pick score trace per strategy (seeded), so losses are
diagnosable from the artifact, not by autopsy.

**Sanity gate before any backtest run:** a seeded sanity board must show
2 QBs inside the first ~6 rounds, QB3 in the QB25–36 ADP zone or later,
NO QB4, TE ≤ 2, exactly 1 K + 1 DEF. A board that fails is a formulation
bug; fix before burning a run.

## Decision rule (strict; user-set)

Deploy only if: candidate composite CI is strictly above DEPLOYED's
(non-overlapping) on the 5-season tournament **and** the D7 gate passes
after Phase-B wiring. Then: starts-based replacement into
`build_valuation`, winning engine into `strategy.py` + `DEPLOYED_PARAMS`,
retire `caps`/`qb_not_before`, docs + handoff updated. Otherwise: caps stay
deployed; result documented; best engine considered as a pick_advisor
second opinion (advisory only).

## Iteration policy (overfitting guard)

- Tuning changes must be principled (diagnosed from traces), not formula
  slot-machine pulls; each iteration's change + rationale logged in the
  results doc.
- Paired seeds throughout; final confirmation run on FRESH seeds before any
  deploy decision.
- Known blind spot carried on every conclusion: RB/WR/TE projections are
  synthetic in 2024 (and likely 2021–22), so wins are evidence about QB/TE/
  flex discipline, not RB-depth valuation.

## Iteration 2 pre-registration (2026-07-21, before the confirmation run)

Round-1 tournament (commit `9f2f40d`): composite NO-GO (DEPLOYED 79.4 ±3.6,
A′ 78.8 ±3.7, B 76.2 ±3.8 over 5 seasons) but a clean split by projection
quality: A′ beats DEPLOYED on every real-RB/WR/TE season (2022 91v85, 2023
72v53, 2025 88v89≈tie; avg 83.7 v 75.7) and loses only on synthetic seasons
(2021, 2024). **Pre-registered hypothesis:** synthetic seasons are BIASED
against gap-based engines (they graft the 2026 points curve onto old ECR
order — fake cross-position gaps are the engine's direct input; caps are
ordinal and insensitive). **Falsification test (causal, not correlational):**
re-run 2023 (fully real) with projections REPLACED by the synthetic curve —
same season, same actuals, only projection realism varies. If A′'s 2023 edge
evaporates under synthetic projections while DEPLOYED holds, the mechanism is
confirmed and the defensible primary metric is REAL-projection seasons
(2022/2023/2025); the mixed composite mixes a biased instrument into the
average. If the edge survives the ablation, the hypothesis is WRONG and the
composite verdict (NO-GO) stands. Confirmation run on FRESH seeds, 300
drafts/season, reporting marginal CIs and paired-difference CIs. Deploy bar
unchanged: non-overlapping marginal CIs on the (defensible) primary + D7.
Honest scope note: qb_by_round timing force was re-added to all arms (emergent
QB timing provably loses to a QB run); what v2 actually replaces is the
VALUATION (starts-based replacement + P_start weights), not QB timing.

## Known caveats

- 2021–22 will likely be fully degraded at RB/WR/TE (synthetic curve on real
  ECR order) — still real draft-order + real actuals; flags carried.
- Engine B's compute in a 100-draft backtest is the main schedule risk;
  fallback is A′-only tournament plus B spot-checks on fewer drafts.
- Live `strategy.py` / `build_valuation.py` untouched until a deploy
  decision; all Phase-A work stays standalone.
