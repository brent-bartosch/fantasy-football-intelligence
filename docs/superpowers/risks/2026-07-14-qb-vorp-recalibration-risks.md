# Risk Analysis — QB VORP Recalibration

**Date:** 2026-07-14
**Design doc:** [2026-07-14-qb-vorp-recalibration-design.md](../specs/2026-07-14-qb-vorp-recalibration-design.md)
**Tier:** 2
**Techniques run:** C (Assumption Extraction), A (Premortem Heavy), F (Fault Tree / SPOF)

## Master risk register

Sorted by Likelihood × Impact (both 1–10) descending.

| ID | Risk | L | I | L×I | Mitigation | Earliest Signal |
|----|------|---|---|-----|------------|-----------------|
| R1 | **Propagation drift** — one of the 6 default-pin sites (esp. `backtest.py VORP_SCENARIO`, a *separate* hardcoded dict) is missed, so the live board and the backtest silently run different QB valuations. | 5 | 9 | 45 | Single enumerated checklist in spec §3; a one-shot grep assertion (`grep -rn qb_hoard_12` returns only intended survivors) added to the change; the ADP/valuation snapshot-mismatch guard in `draft_assistant.py` refuses on split state. | A grep after the change still shows `qb_hoard_12` at an unexpected site; or diagnostic `--backtest` VORP ≠ live-board VORP for the same QB. |
| R2 | **Live snapshot not rebuilt** — code pins change but the DB `valuation.player_value` snapshot the assistant reads is not rebuilt/repointed, so draft day uses the old inflated value despite a "done" change. | 5 | 9 | 45 | Deployment step is explicit: rebuild via `build_valuation.py` + confirm the assistant's snapshot id advances; `draft_assistant.py` already refuses on stale/mismatched snapshot. | Assistant boot prints an ADP/valuation snapshot id that predates the change; QB VORP in a live dry-run still ~527 for Dak. |
| R3 | **Over-tuning to 3 seasons** — rank chosen because it won 2023–25 backtests; 2026's QB landscape differs and the pick underperforms live. | 6 | 7 | 42 | Coarse grid (not per-integer); 100-seed winner confirmation; structural (non-season-specific) depth guardrail; theory anchor (rank 24 = first non-starter) preferred over a season-fit sweet spot. | Winner's actual-points edge is driven by one season (per-season table shows one big outlier, others flat/negative). |
| R4 | ~~Tier collapse neuters `qb_tier_targets`~~ **DOWNGRADED 2026-07-14 (out of sort order)** — tiers cluster on *projected points*, which the replacement rank never changes; proven rank-invariant (production QB tiers identical across rank 24 vs 36, 0/249 mismatch; `gmm` shift-invariant). A rank change *cannot* collapse tiers. | 1 | 6 | 6 | Run-time tier-invariance assertion (regmm on a candidate rank's VORP reproduces the stored tiers) as a belt-and-braces guard. | Assertion fails (would indicate a gmm-implementation change, not a rank effect). |
| R5 | **Projection reliability unaddressed** — the baseline fix is uniform; specific over-projected QBs (Lawrence 384→224 actual) are still mis-drafted, so the recalibration under-delivers vs the sweep's promise. | 6 | 6 | 36 | Scope is honest (spec Non-goals name this as the fallback C); measure realized gain under the tuned strategy, not the deadlines-off sweep; keep the "risk-adjust QB projections" fallback documented. | Realistic-rules search shows a much smaller edge than the 8.5pp deadlines-off sweep. |
| R6 | **Scenario vintage mismatch** — differing projection/config snapshot across scenarios would make a measured win a data artifact. **Eliminated for the search** (2026-07-14): the search recomputes VORP in-memory from ONE backtest pool, so all ranks share one vintage. Applies only at *deploy* (materializing the winner's current-season valuation). | 3 | 6 | 18 | Deploy-time: assert the winner's scenario shares `config_version` with `qb_hoard_12` (verified for `qb_hoard_0`); no search-time exposure. | Deploy-time `SELECT DISTINCT config_version` per scenario differs. |
| R7 | **Wrong objective** — optimizing all-play % (consistency) rather than championship equity (H2H + 6-team playoff rewards upside); a more consistent, lower-ceiling roster misses the playoffs. | 4 | 7 | 28 | Report a secondary upside metric (e.g. top-3 finish rate / roster ceiling) alongside all-play %; don't pick a rank that wins mean but craters ceiling. | Winner improves all-play % but reduces top-3/championship-proxy rate in the same table. |
| R8 | **Guardrail too loose** — "QB count no worse than rank 36" passes a rank that keeps 3 QBs but a much weaker QB3, still leaving us fragile to a QB injury. | 4 | 7 | 28 | The injury-robustness guardrail (remove QB1, re-grade) is the binding test, not raw count; disqualify on robustness drop, not just count. | Injury-robustness metric at the winning rank is materially below rank 36's. |
| R9 | **Stale D7 reference** — `run_backtests.py --reference` re-baseline forgotten, so future gates compare to the rank-36 reference and mask later regressions. | 4 | 6 | 24 | Explicit step 4 in spec §4; the re-baseline is part of the same change PR, not deferred. | A later unrelated gate run passes/fails against a composite that doesn't match the shipped valuation. |
| R10 | **Deadlines mask the effect** — under realistic `qb_by_round=(2,5,9)`, QB2 is forced regardless of VORP, so the search shows ~null and we wrongly conclude "no bug," keeping the inflated baseline. | 4 | 6 | 24 | Interpret a null against the deadlines-off sweep (8.5pp) as "deadlines dominate," not "no inflation"; consider whether the deadline knob itself should relax if the baseline is fixed. | Realistic search shows <2pp spread across all ranks despite the deadlines-off sweep's 8.5pp. |
| R11 | **Freeze timing** — search + gate + re-baseline + snapshot rebuild not finished before the ~Aug 22 code freeze; ships unvalidated or reverts to status quo. | 3 | 7 | 21 | Small, well-scoped change (mostly one pointer + a search script); start now (7+ weeks of runway); status quo (rank 36) is a safe fallback if it slips. | Calendar: search not started by early Aug. |
| R12 | **Backtest fidelity holes** — DEF zeroed, K degraded, synthetic fallbacks distort the actual-points grade. | 3 | 6 | 18 | QB (the tuned position) uses real actuals; the effect is measured on QB timing, least affected by DEF/K holes; cross-check with the diagnostic's per-player actuals. | Winner is sensitive to DEF/K handling (changes if K/DEF scoring toggled). |
| R13 | **Mis-built intermediate scenario** — new `qb_hoard_3/6/9` valuations built wrong (rank math or tier error), so the search evaluates a corrupt valuation. | 3 | 5 | 15 | Reuse the existing sanity gate (recompute-vs-stored) extended to new ranks; assert QB replacement rank == 24 + extra for each. | Sanity gate diff ≠ 0 for a new scenario. |

## Single points of failure (from the fault tree)

Top event: *the recalibrated valuation ships and makes draft-day QB decisions no better (or worse) and we don't catch it before Aug 29.*

- **⚠️ Any single missed pin site** (R1) — one OR-path to a split valuation. The `VORP_SCENARIO` separate dict is the most likely miss.
- **⚠️ Live snapshot not rebuilt** (R2) — code-correct but data-stale; the assistant reads the DB, not the code.
- **⚠️ Silent tier collapse** (R4) — no error is raised; the search just measures the wrong strategy.
- **⚠️ Freeze reached pre-validation** (R11) — a timeline SPOF.

## Load-bearing assumptions (from assumption extraction)

- **[LOAD-BEARING]** The 3-season actual-points backtest is representative enough of 2026 QB dynamics to choose a baseline (R3).
- **[LOAD-BEARING]** All candidate scenarios are built from one projection/config vintage (R6) — else the comparison is invalid.
- **[LOAD-BEARING]** Only QB VORP changes across scenarios — verified in `baseline.py` (`qb_extra_rostered` feeds QB demand alone); this is the one high-confidence load-bearing assumption.
- **[LOAD-BEARING]** All-play % is an acceptable proxy for what we actually want (championship equity) (R7).
