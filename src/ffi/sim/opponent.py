"""Opponent pick model (Phase 3 / Task 6): two-stage draft-pick simulation.

Stage 1 — position choice: a slot/round's `SlotPriors.pos_share` gives the
prior probability of an opponent taking each position, feasibility-masked
(a starter/flex slot that can't be filled with the picks remaining is never
sacrificed for a luxury pick — see `required_picks`/`feasible`) and
roster-damped (repeat positions like a 4th QB or 2nd TE get suppressed via
`ROSTER_DAMP`, since real drafters stop hoarding a position well before it's
mathematically infeasible to do so).

Stage 2 — player choice within the chosen position: a softmax over the
within-position candidate's rank in `avail_by_pos[pos]`, restricted to the
top `CAND_WINDOW` candidates, with temperature `TAU`. Rank 0 (best available)
is favored but not guaranteed — this lets an opponent occasionally reach for
a slightly-lower-ranked player, matching real draft noise.

Contract on `avail_by_pos`: the draft engine (Task 7) maintains each
`avail_by_pos[pos]` list sorted with real-ADP players first (ascending ADP),
followed by ADP-less players (Sleeper's undrafted sentinel, mapped to `None`
by `ffi.sim.pool.build_pool`) ordered by `proj_points` descending. This
module never re-sorts; it trusts that ordering and only slices the head
(`[:CAND_WINDOW]`).

Fail-loud: if every position is either unavailable or infeasible given the
picks remaining, `opponent_pick` raises `ValueError` rather than silently
returning a nonsensical pick — that state should never occur in a
well-formed draft, so seeing it means an upstream invariant (roster
counting, picks-remaining bookkeeping) is broken.
"""
from dataclasses import dataclass

import numpy as np

from ffi.sim.priors import POSITIONS, SlotPriors
from ffi.sim.pool import PoolPlayer

TAU = 1.8  # softmax temperature over within-position candidate rank
CAND_WINDOW = 12  # only the top-N available at a position are pickable

# Multiplier applied to a position's prior share once the opponent's count at
# that position crosses a threshold — the HIGHEST crossed threshold wins.
# Not a hard cutoff (except where the multiplier is 0.0): a drafter hoarding
# a position becomes steadily less likely to keep doing it, matching real
# draft behavior, rather than flipping from "normal" to "banned" at one count.
ROSTER_DAMP = {
    "QB": {3: 0.15, 4: 0.0},
    "TE": {2: 0.3, 4: 0.0},
    "K": {1: 0.02, 2: 0.0},
    "DEF": {1: 0.02, 2: 0.0},
}

# Starter slots per position (+1 FLEX eligible for RB/WR/TE). Deliberately
# separate from any STARTERS in ffi.valuation — that module's STARTERS serves
# a different (non-flex-aware) purpose and the two must not be conflated.
STARTERS = {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}


@dataclass(frozen=True)
class OpponentParams:
    tau: float = TAU
    cand_window: int = CAND_WINDOW
    # Per-position prior-share multiplier indexed by CURRENT count at that
    # position, e.g. (("QB", (3.0, 1.4, 1.0)),) => a slot holding 0 QBs has
    # its QB prior share ×3.0, holding 1 => ×1.4, holding >=2 => ×1.0 (past
    # the tuple's end, the LAST entry extends). () = mechanism off = bit-
    # identical legacy behavior.
    #
    # Shipped default (("QB", (2.0, 1.5, 0.5)),): the Phase 4 Task 4 fit
    # (scripts/calibrate_opponents.py --fit, reports/opponent-calibration-
    # 2026-07-10.md; plan .superpowers/sdd/task-4-brief.md). It pulls opponent
    # QB1 timing from an un-calibrated round 2.78 to 1.73 (historical
    # seasons-weighted mean 1.83) and QB2 to 4.50 (historical 4.45); the s2=0.5
    # tail damps a 3rd QB but cannot fully reach history's very-late QB3
    # (measured 8.86 vs 10.78 -- a documented mechanism limitation, weighted
    # least in the fit objective). `pos_need_scale=()` still selects legacy.
    pos_need_scale: tuple[tuple[str, tuple[float, ...]], ...] = (
        ("QB", (2.0, 1.5, 0.5)),
    )


DEFAULT_OPPONENT_PARAMS = OpponentParams()


def required_picks(counts: dict) -> int:
    """How many more picks are needed to fill every starter slot + FLEX.

    FLEX (RB/WR/TE) is considered filled once any of those three positions
    has been drafted beyond its own starter requirement (a surplus RB, WR,
    or TE can occupy FLEX) — it does not require a dedicated pick.
    """
    need = sum(max(0, req - counts.get(p, 0)) for p, req in STARTERS.items())
    flex_surplus = (
        max(0, counts.get("RB", 0) - 2)
        + max(0, counts.get("WR", 0) - 3)
        + max(0, counts.get("TE", 0) - 1)
    )
    return need + (0 if flex_surplus >= 1 else 1)


def feasible(counts: dict, pos: str, picks_left_after: int) -> bool:
    """Would taking `pos` now still leave enough picks to fill the roster?"""
    c2 = dict(counts)
    c2[pos] = c2.get(pos, 0) + 1
    return required_picks(c2) <= picks_left_after


def opponent_pick(
    avail_by_pos: dict,
    priors: SlotPriors,
    slot: int,
    round_: int,
    counts: dict,
    picks_left_after: int,
    rng: np.random.Generator,
    params: OpponentParams | None = None,
) -> PoolPlayer:
    """Simulate one opponent draft pick.

    `avail_by_pos[pos]` must be pre-sorted by the caller: real-ADP players
    ascending by ADP first, then ADP-less players by `proj_points`
    descending (see module docstring) — this function only slices the head.

    Raises `ValueError` if no position is both available and feasible given
    `picks_left_after` (should never happen in a well-formed draft).
    """
    params = params or DEFAULT_OPPONENT_PARAMS
    share = priors.pos_share[(slot, round_)]
    scale_map = dict(params.pos_need_scale)
    weights = {}
    for pos in POSITIONS:
        cands = avail_by_pos.get(pos) or []
        if not cands or not feasible(counts, pos, picks_left_after):
            continue
        w = share.get(pos, 0.0)
        damp = ROSTER_DAMP.get(pos, {})
        crossed = [t for t in damp if counts.get(pos, 0) >= t]
        if crossed:
            w *= damp[max(crossed)]
        sc = scale_map.get(pos)
        if sc:
            w *= sc[min(counts.get(pos, 0), len(sc) - 1)]
        weights[pos] = w

    if not weights:
        raise ValueError(
            f"no feasible position for slot {slot} round {round_} counts {counts}"
        )

    total = sum(weights.values())
    if total <= 0:  # all dampened to zero (or all priors zero) -> uniform over feasible
        weights = {p: 1.0 for p in weights}
        total = float(len(weights))

    positions = sorted(weights)  # sorted for determinism given a fixed rng state
    probs = [weights[p] / total for p in positions]
    pos = positions[rng.choice(len(positions), p=probs)]

    cands = avail_by_pos[pos][: params.cand_window]
    logits = np.exp(-np.arange(len(cands)) / params.tau)
    return cands[rng.choice(len(cands), p=logits / logits.sum())]
