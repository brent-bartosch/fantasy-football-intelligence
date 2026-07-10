"""Seeded snake draft engine (Phase 3 / Task 7): state, legality, orchestration.

Orchestrates a full 12-team, 19-round (228-pick) snake draft. A single
`np.random.default_rng(seed)` drives everything — the franchise-slot-to-
draft-position permutation AND every opponent pick (Task 6's `opponent_pick`
draws from the same generator, in strict overall-pick order) — so the same
seed reproduces a byte-identical draft. This is the property under test in
`test_draft_is_deterministic_by_seed` / the hypothesis property test.

Franchise slots (the stable Yahoo team seats that `SlotPriors` is keyed by)
are randomly permuted onto draft positions (1-12, the snake seat order) each
draft, since the 2026 real draft order is unknown and downstream conclusions
must marginalize over it. `our_position`, when given, pins OUR seat: our
franchise slot occupies that draft position and the remaining 11 franchise
slots are permuted across the remaining 11 draft positions. When omitted,
our slot's draft position is itself randomized by the same permutation.

Our pick is produced by `our_pick_fn` (Task 8 supplies the real strategy;
tests use a greedy max-VORP feasible stand-in). Every other seat's pick comes
from `ffi.sim.opponent.opponent_pick`, using the `SlotPriors` of whichever
franchise slot occupies that seat. Fail-loud: `our_pick_fn`'s return value is
re-validated against the same availability/feasibility rules opponents are
held to (available — not already drafted — and `feasible` given the seat's
remaining picks) and a `ValueError` is raised on violation. A strategy bug
must never be allowed to silently corrupt a simulated draft.

Availability bookkeeping: per the contract `ffi.sim.opponent` documents,
`avail_by_pos[pos]` must stay sorted real-ADP-ascending-then-None-ADP-by-
proj_points-descending. This module owns that invariant: it sorts each
position's full player list once up front, keeps a `taken` set of drafted
refs, and derives the live `avail_by_pos` view each pick by filtering the
(already-sorted) full lists against `taken` — filtering preserves sort
order, so the contract holds without ever re-sorting.

NOTE on a brief deviation: the brief's illustrative snake-order test asserts
`snake_position(228) == (19, 1)`. That's inconsistent with its own other
four examples (verified by hand and by direct computation): round 19 is odd,
and the brief's examples establish odd rounds run ascending 1->12 (round 1:
overall 1 -> position 1, overall 12 -> position 12) while even rounds run
descending (round 2: overall 13 -> position 12, overall 24 -> position 1).
Under that rule the LAST pick of round 19 (overall 228, the 12th pick within
the round) is position 12, not 1 — `(19, 1)` is actually `snake_position(217)`,
the FIRST pick of round 19 (round 19 starting at position 1, mirroring round
1, since both are odd). This module implements the mathematically consistent
rule (matching all four of the brief's other examples); the test suite
documents and corrects the fifth example rather than encoding a bug.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ffi.sim.opponent import feasible, opponent_pick
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors

TEAMS = 12
ROUNDS = 19
TOTAL_PICKS = TEAMS * ROUNDS  # 228


@dataclass
class DraftResult:
    rosters: dict[int, list[PoolPlayer]]  # draft position (1-12) -> 19 players
    picks: list[dict] = field(default_factory=list)
    # [{overall, position_slot, franchise_slot, pos, ref, name}]
    our_position: int = 0
    slot_of_position: dict[int, int] = field(
        default_factory=dict
    )  # draft position -> franchise slot


# (avail_by_pos, round, counts, picks_left_after) -> player — OUR seat only
PickFn = Callable[[dict[str, list[PoolPlayer]], int, dict[str, int], int], PoolPlayer]


def snake_position(overall: int) -> tuple[int, int]:
    """1-indexed overall pick number -> (round 1-19, draft position 1-12).

    Odd rounds run ascending (position 1 picks first, 12 picks last); even
    rounds reverse (12 first, 1 last) — standard snake order, with position
    p's back-to-back picks at a round boundary (e.g. position 12 picks last
    in round 1 and first in round 2).
    """
    round_ = (overall - 1) // TEAMS + 1
    idx = (overall - 1) % TEAMS  # 0-indexed pick-within-round
    position = idx + 1 if round_ % 2 == 1 else TEAMS - idx
    return round_, position


def _build_sorted_pool(pool: list[PoolPlayer]) -> dict[str, list[PoolPlayer]]:
    """Group `pool` by position, each list sorted per the avail_by_pos
    contract: real-ADP players ascending by ADP, then None-ADP players by
    proj_points descending."""
    by_pos: dict[str, list[PoolPlayer]] = defaultdict(list)
    for p in pool:
        by_pos[p.position].append(p)
    for pos in by_pos:
        by_pos[pos].sort(
            key=lambda p: (
                p.adp is None,
                p.adp if p.adp is not None else 0.0,
                -p.proj_points,
            )
        )
    return dict(by_pos)


def _avail_view(
    sorted_pool: dict[str, list[PoolPlayer]], taken: set
) -> dict[str, list[PoolPlayer]]:
    """Live availability view: filters each (already-sorted) position list
    against `taken`, preserving order — never re-sorts."""
    return {
        pos: [p for p in plist if p.ref not in taken]
        for pos, plist in sorted_pool.items()
    }


def _resolve_slot_of_position(
    rng: np.random.Generator,
    our_franchise_slot: int,
    our_position: int | None,
) -> tuple[dict[int, int], int]:
    """Permute franchise slots 1-12 onto draft positions 1-12. Returns
    (slot_of_position, resolved_our_position)."""
    all_slots = list(range(1, TEAMS + 1))
    if our_position is not None:
        remaining_slots = [s for s in all_slots if s != our_franchise_slot]
        remaining_positions = [p for p in all_slots if p != our_position]
        perm = rng.permutation(remaining_slots)
        slot_of_position = {our_position: our_franchise_slot}
        for pos, slot in zip(remaining_positions, perm):
            slot_of_position[pos] = int(slot)
        return slot_of_position, our_position

    perm = rng.permutation(all_slots)
    slot_of_position = {pos: int(perm[pos - 1]) for pos in all_slots}
    resolved_our_position = next(
        pos for pos, slot in slot_of_position.items() if slot == our_franchise_slot
    )
    return slot_of_position, resolved_our_position


def run_draft(
    pool: list[PoolPlayer],
    priors: SlotPriors,
    our_pick_fn: PickFn,
    seed: int,
    our_franchise_slot: int = 12,
    our_position: int | None = None,
) -> DraftResult:
    """Simulate one full 12-team, 19-round snake draft.

    A single `np.random.default_rng(seed)` drives the franchise-slot
    permutation and every opponent pick, in overall-pick order — same seed,
    same pool, same priors -> byte-identical draft.
    """
    rng = np.random.default_rng(seed)
    slot_of_position, resolved_our_position = _resolve_slot_of_position(
        rng, our_franchise_slot, our_position
    )

    sorted_pool = _build_sorted_pool(pool)
    taken: set = set()
    rosters: dict[int, list[PoolPlayer]] = {pos: [] for pos in range(1, TEAMS + 1)}
    counts: dict[int, dict[str, int]] = {pos: {} for pos in range(1, TEAMS + 1)}
    picks: list[dict] = []

    for overall in range(1, TOTAL_PICKS + 1):
        round_, position = snake_position(overall)
        franchise_slot = slot_of_position[position]
        seat_counts = counts[position]
        picks_left_after = ROUNDS - round_
        avail_by_pos = _avail_view(sorted_pool, taken)

        if position == resolved_our_position:
            pick = our_pick_fn(avail_by_pos, round_, seat_counts, picks_left_after)
            available_refs = {p.ref for p in avail_by_pos.get(pick.position, [])}
            if pick.ref not in available_refs:
                raise ValueError(
                    f"our_pick_fn returned an unavailable/already-drafted player "
                    f"{pick.ref!r} ({pick.position}) at overall pick {overall} "
                    f"(round {round_}, position {position})"
                )
            if not feasible(seat_counts, pick.position, picks_left_after):
                raise ValueError(
                    f"our_pick_fn returned an infeasible pick {pick.ref!r} "
                    f"({pick.position}) at overall pick {overall}: "
                    f"counts={seat_counts}, picks_left_after={picks_left_after}"
                )
        else:
            pick = opponent_pick(
                avail_by_pos,
                priors,
                franchise_slot,
                round_,
                seat_counts,
                picks_left_after,
                rng,
            )

        taken.add(pick.ref)
        rosters[position].append(pick)
        seat_counts[pick.position] = seat_counts.get(pick.position, 0) + 1
        picks.append(
            {
                "overall": overall,
                "position_slot": position,
                "franchise_slot": franchise_slot,
                "pos": pick.position,
                "ref": pick.ref,
                "name": pick.name,
            }
        )

    return DraftResult(
        rosters=rosters,
        picks=picks,
        our_position=resolved_our_position,
        slot_of_position=slot_of_position,
    )
