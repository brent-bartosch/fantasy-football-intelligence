# Opponent QB-timing calibration (Phase 4 Task 4) -- 2026-07-10

**Audience:** anyone re-deriving why the sim's opponents now take QB1 a full
round earlier, and why that change is trustworthy.
**What this is:** the durable home for the fit evidence behind the shipped
default `OpponentParams.pos_need_scale = (("QB", (2.0, 1.5, 0.5)),)`. The raw
fit run lives in `reports/opponent-calibration-2026-07-10.md` (gitignored); this
doc is the committed record. The user-facing consequence -- the re-adjudicated
QB-timing policy -- is the post-calibration addendum in
`2026-07-10-strategy-conclusions.md`.
**Reproduce the fit:**
`uv run python scripts/calibrate_opponents.py --fit --drafts 200 --seed 20260710`
(scenario `qb_hoard_12`, 72-candidate default grid, common random numbers).

---

## 1. The measured problem: opponents drafted QB1 too late

The sim's opponent pick model leans on a national 2-QB ADP softmax. Measured
against the room's own history, its opponents were leaving QBs on the board about
a round too long:

| QB slot | historical (seasons-weighted league mean) | Task 2 baseline (un-scaled, uniform sample) |
|---|---|---|
| QB1 | 1.83 | 2.78 |
| QB2 | 4.45 | 6.81 |
| QB3 | 10.78 | 8.93 |

Two baseline numbers for QB1 appear in the record and mean different things:
the **uniform-sample** baseline is **2.78** (Task 2's `measure_qb_timing`, every
slot sampled equally, opponents only); the **biased 198-draft farm-sample** the
assumption audit reported was **2.84** (the farm's own draft mix, which
over-weights certain slots). Both say the same thing -- opponents took QB1 ~a
full round later than the room's 1.83 -- and the calibration target is the
historical 1.83, so the discrepancy between 2.78 and 2.84 does not affect the
fit.

## 2. Mechanism: conditional persistence, NOT contagion

The knob added is `pos_need_scale`: a **roster-state multiplier on a slot's own
QB prior share**, indexed by how many QBs that slot already holds --

- ×2.0 while the slot holds **0** QBs,
- ×1.5 while it holds **1**,
- ×0.5 while it holds **>=2** (the tuple's last entry extends).

This is **conditional persistence** -- a slot's *own* current QB count drives its
*next* QB propensity (empty-handed slots reach for QB1 sooner; slots with two
QBs stop reaching). It is explicitly **not contagion** -- it does not let one
slot's QB pick raise a *different* slot's QB probability.

That distinction is load-bearing and was settled empirically first.
`2026-07-10-qb-run-contagion.md` tested the contagion hypothesis directly:
observed runs of >=3 QBs were **46 vs a within-round-shuffle null of 44.9 +/- 4.5
(p ~= 0.45)** -- QB "runs" in this league are structural round-level traffic, not
pick-to-pick panic. Its verdict was **do NOT build a contagion mechanism; the
real gap is LEVEL calibration** (opponents taking QB1 at ~2.8 vs the room's
1.83). `pos_need_scale` is exactly that level fix and nothing more -- it shifts
*when* the average slot reaches its first QB without inventing a between-slot
coupling the data does not support.

## 3. Fit -- top 5 trials

Objective (lower is better):
`3*|m1-h1| + 2*|m2-h2| + 1*|m3-h3| + 0.5*per_slot_QB1_MAE`
(QB1 weighted hardest; QB3 weighted least because it is the least reachable --
see the FAIL below). 72-candidate grid, 200 drafts/candidate, common random
numbers, scenario `qb_hoard_12`.

| rank | scale (s0,s1,s2) | QB1 | QB2 | QB3 | per-slot QB1 MAE | objective |
|---|---|---|---|---|---|---|
| 1 | (2, 1.5, 0.5) | 1.73 | 4.50 | 8.86 | 0.20 | 2.414 |
| 2 | (2, 1.5, 0.75) | 1.73 | 4.50 | 8.33 | 0.20 | 2.937 |
| 3 | (1.5, 2, 0.5) | 2.07 | 4.38 | 8.77 | 0.31 | 3.010 |
| 4 | (2, 1.5, 1) | 1.73 | 4.50 | 7.90 | 0.20 | 3.374 |
| 5 | (1.5, 1.5, 0.5) | 2.07 | 4.92 | 9.07 | 0.31 | 3.532 |

**Winner: `pos_need_scale = (("QB", (2.0, 1.5, 0.5)),)`** (rank 1).

## 4. Acceptance verdicts

- **PASS** -- QB1 mean **1.73** vs historical 1.83 (|Δ|=0.10, bar ±0.25). This is
  the hard STOP bar; it is met by multiple grid points, so the fit was not
  forced onto a single knife-edge cell.
- **PASS** -- QB2 mean **4.50** vs historical 4.45 (|Δ|=0.05, bar ±0.5).
- **FAIL (honest)** -- QB3 mean **8.86** vs historical 10.78 (|Δ|=1.92, bar
  ±0.5). This is a **mechanism limitation, not a tuning miss**: s2=0.5 is the
  strongest late-3rd-QB damp on the grid and still cannot push the 3rd QB as
  late as real drafters wait. The grid was NOT widened to chase it (the brief
  forbids chasing this residual), and QB3 is weighted least (×1) in the
  objective precisely because it is the least reachable. The direction of the
  residual matters and is carried into the strategy doc's addendum: the sim's
  opponents take a 3rd QB ~2 rounds EARLIER than the room does, so the sim
  OVERSTATES third-QB scarcity.
- **report-only** -- per-slot QB1 MAE = 0.20 (no hard bar; the priors carry slot
  identity while the knob is global, so some per-slot spread is expected).
- **PASS** -- non-QB max pos-share deviation-from-uniform **0.272** vs baseline
  0.255 (the mix *outside* the QB target must not get materially worse; +0.05
  tolerance). The overall max deviation did rise 0.255 -> 0.350, but that growth
  is **entirely the intended QB R1-3 share lift** (R1-3 QB share 0.411 -> 0.516);
  decomposition confirmed no position is starved -- R1-3 RB deviation actually
  improved 0.176 -> 0.107.

## 5. Before/after per-slot QB1 (round, opponents only, 200 drafts, seed 20260710)

| slot | baseline QB1 | calibrated QB1 | historical QB1 | n |
|---|---|---|---|---|
| 1 | 2.79 | 1.61 | 1.88 | 200 |
| 2 | 2.56 | 1.60 | 1.56 | 200 |
| 3 | 3.49 | 1.88 | 1.94 | 200 |
| 4 | 2.58 | 1.64 | 1.75 | 200 |
| 5 | 3.19 | 2.09 | 2.19 | 200 |
| 6 | 2.66 | 1.60 | 1.62 | 200 |
| 7 | 2.94 | 2.00 | 1.56 | 200 |
| 8 | 2.29 | 1.50 | 1.38 | 200 |
| 9 | 2.79 | 1.69 | 1.75 | 200 |
| 10 | 2.48 | 1.51 | 1.81 | 200 |
| 11 | 2.85 | 1.90 | 2.62 | 200 |

Every slot moves from ~2.3-3.5 down toward its historical QB1 round; the fit
tracks the per-slot targets closely (MAE 0.20) despite the knob being global.

## 6. D7 gate + the composite recompute chain

Adopting the calibration as the shipped default, `scripts/run_backtests.py
--gate` **PASSED (exit 0)**: our-seat composite **0.5330** vs the active
reference 0.5297, band 0.0196, threshold = 0.5297 − 0.0196 = **0.5101**;
0.5330 >= 0.5101. Per the D7 protocol, a passing gate means **step 1 applies --
done, note the composite, NO reference rebuild**. The active reference stays
`ref_id 2` (composite 0.5297 / band 0.0196, git 651191dc).

The composite our-seat all-play moved through three causes, all pre-calibration
accounting followed by the calibration itself:

| stage | composite | cause |
|---|---|---|
| pre-Task-1 history | 0.5297 | the stored reference (ref_id 2) |
| post-Task-1 recompute | 0.5243 | Task 1's `ORDER BY` tie-order fix shifted deterministic draft tie-breaks (controller-verified) |
| post-calibration | 0.5330 | adopting `pos_need_scale=(2.0,1.5,0.5)` |

Read the chain carefully: the calibration did **not** degrade our seat -- from
the post-Task-1 0.5243 it *raised* the composite to 0.5330 (+0.0087). Calibrated
opponents taking QBs earlier did not cost our seat measured all-play; if
anything it helped. The whole excursion (0.5243 -> 0.5330) sits comfortably
inside the 0.0196 band around the 0.5297 reference, so no rebuild triggered.

### Per-season detail (reference vs calibrated gate, mean over the 4 REF strategies)

| season | reference | calibrated | Δ |
|---|---|---|---|
| 2023 | 0.4926 | 0.4864 | −0.0062 |
| 2024 | 0.5666 | 0.5692 | +0.0026 |
| 2025 | 0.5299 | 0.5433 | +0.0134 |

All shifts are sub-band and within per-cell SE (~0.012-0.015); the four REF
strategies still cluster within ~0.014 of one another. Informational only -- the
gate passed, so the rebuild-vs-STOP branch was never entered.

---

## Provenance

- Fit + adoption: commit `ece1500` (branch `phase4-assistant`), full suite 331
  passed.
- Shipped default: `src/ffi/sim/opponent.py` `OpponentParams.pos_need_scale`.
- Post-adoption uniform audit (`scripts/sim_report.py`, independent
  `measure_qb_timing` recompute): sim league-wide QB1 mean **1.77** vs historical
  1.83 -- a hard regression check, passing.
- Full fit trace (all 72 trials): `reports/opponent-calibration-2026-07-10.md`
  (gitignored).
