# QB draft behavior: run contagion & draft-order accuracy — 2026-07-10

**Question (user):** the draft's chaos includes apparent "QB runs" — do we need a contagion
mechanism (a QB pick raising the next pick's QB probability) in the opponent model?
**Reproduce:** `uv run python scripts/research_qb_draft_behavior.py` (deterministic; permutation
null seeded, 500 shuffles). Data: all 16 NAJEE drafts, rounds 1–19 (3,648 picks).

## 1. Raw conditional lift exists…

| band | P(QB | prev pick QB) | P(QB | prev pick not QB) | raw lift |
|---|---|---|---|
| R1–3 | 0.443 (n=228) | 0.364 (n=332) | ×1.22 |
| R4–8 | 0.215 (n=191) | 0.191 (n=769) | ×1.12 |
| R9+  | 0.240 (n=333) | 0.141 (n=1,779) | ×1.70 |

QB streaks observed: 530 runs; length ≥2: 141, ≥3: 46, ≥4: 17, max 7.

## 2. …but it is fully explained by round-level QB density

Null model: shuffle positions **within each round of each draft** — this preserves every round's
QB share (the structural driver: 12 teams each needing 2–3 QBs squeeze through the same rounds)
while destroying any pick-to-pick contagion.

**Runs of ≥3 QBs: observed 46 vs null 44.9 ± 4.5 → p ≈ 0.45.**

The raw conditional lift in §1 is a within-band composition effect (after-QB observations
concentrate in QB-dense rounds), not contagion. **Verdict: QB runs in this league are structural
traffic, not panic. Do NOT build a contagion mechanism in the opponent pick model.** The opponent
model's per-slot round-level position shares (`ffi/sim/priors.py`) already capture the structural
component.

The real, separate gap is LEVEL calibration: the sim-farm assumption audit measures simulated
opponents taking QB1 at mean round ~2.84 vs the room's historical 1.83 (`qb_timing_by_slot`) —
the room is more QB-early than the national 2QB ADP the pick model's softmax leans on. That is
Phase 4's first task (see `docs/2026-07-10-PHASE4-HANDOFF.md` §2.1); it is a distribution-matching
problem, not a contagion problem.

## 3. Context: how well did the room's QB draft order predict finishes?

QB draft order vs end-of-season league-scored points (weeks 1–14, drafted QBs, 2019–2025):

| season | n QBs | Spearman | top-5 drafted still top-5 | busts (top-8 drafted → finished >QB16) |
|---|---|---|---|---|
| 2019 | 45 | 0.577 | 1/5 | Andrew Luck, Baker Mayfield |
| 2020 | 48 | 0.782 | 3/5 | Dak Prescott, Drew Brees, Matt Ryan |
| 2021 | 39 | 0.731 | 2/5 | Russell Wilson |
| 2022 | 45 | 0.726 | 2/5 | Russell Wilson |
| 2023 | 44 | 0.659 | 4/5 | Justin Fields, Joe Burrow |
| 2024 | 49 | 0.803 | 3/5 | C.J. Stroud, Anthony Richardson Sr., Dak Prescott |
| 2025 | 46 | 0.611 | 1/5 | Lamar Jackson, Jayden Daniels, Joe Burrow |

The room's preseason ordering is decent (ρ 0.58–0.80) but **1–3 of the top-8 drafted QBs bust
outright every single year**, and 1–4 of the top-5 drafted fall out of the top-5 finish. This is
the quantified argument for tier-depth over rank precision (Boris-Chen-style cohorts on our
league-adjusted values): tier 2 currently runs 11 deep with ADP stretching to 44 — bust
diversification that a precise ranking cannot provide.

## Caveats

- Streak test pools all 16 seasons; per-era contagion (e.g., only in recent seasons) was not
  tested — n gets thin fast, and the pooled null is comfortably centered on the observation.
- "Bust" uses a fixed >QB16 finish cutoff among drafted QBs; injuries and retirements (Luck)
  are included deliberately — draft-day risk includes them.
- Draft order is the room's revealed preseason valuation; it is not identical to contemporaneous
  market ADP (which we lack pre-2023).
