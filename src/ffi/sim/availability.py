"""VONA (Value Of Next Availability) forecast layer (Phase 4 / Task 8).

Monte-Carlo forward-simulates the calibrated opponent model
(`ffi.sim.opponent.opponent_pick`) across the opponent picks that happen
between now and our next turn, producing per-player survival probabilities
and per-position expected-best-VORP forecasts. This sits on the draft
assistant's between-picks path (Task 12/13 consume `AvailabilityForecast`
and `vona`), so it is a pure function of its arguments -- no DB access, no
I/O -- and deterministic given a seed.

Each rollout `k` (of `n_rollouts`) draws its own `np.random.default_rng(seed
+ k)` and walks `upcoming` -- `[(franchise_slot, round_, counts), ...]` in
pick order -- calling `opponent_pick` once per entry against a rollout-local
`taken` set (filtered through `ffi.sim.draft._avail_view`, reused rather than
reimplemented). A seat's `counts` dict is copied once per rollout the first
time that franchise slot appears in `upcoming`, then evolved in place as that
rollout's simulated picks land for that seat (mirroring
`ffi.sim.draft.run_draft`'s own bookkeeping) -- so a seat that picks twice in
the same rollout (e.g. draft positions 1 and 12 at a snake turn boundary) has
its second pick see the first's effect. The caller's `avail_by_pos` lists and
`upcoming` counts dicts are never mutated.

`survival` is populated only for the head `CAND_WINDOW * 2` players of each
position in the CALLER's `avail_by_pos` (deeper players are, by construction,
never a plausible opponent target within one turn, so tracking their
survival would bloat the dict for no signal). `expected_best_vorp`, by
contrast, is computed from each rollout's live (post-simulated-picks)
availability view directly, not restricted to the tracked window -- this is
both correct and simpler than special-casing the window's edge.

Empty `upcoming` (a back-to-back snake turn, e.g. draft position 12's
round 1 -> round 2) short-circuits: nothing can be taken before our next
pick, so `survival` is 1.0 for every tracked player, `expected_best_vorp`
equals the current best, and `vona` is 0.0 everywhere.
"""
from dataclasses import dataclass

import numpy as np

from ffi.sim.draft import ROUNDS, _avail_view
from ffi.sim.opponent import CAND_WINDOW, opponent_pick
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors

# Survival is tracked for the head this-many players per position -- deep
# enough that no opponent could plausibly reach past it in one turn (twice
# the stage-2 candidate window `opponent_pick` itself draws from).
_SURVIVAL_WINDOW = CAND_WINDOW * 2


@dataclass(frozen=True)
class AvailabilityForecast:
    n_rollouts: int
    n_upcoming: int  # opponent picks simulated before our next pick
    survival: dict[str, float]  # ref -> P(still available at our next pick)
    expected_best_vorp: dict[str, float]  # pos -> E[max vorp still available]


def _best_vorp(plist: list[PoolPlayer]) -> float:
    return max((p.vorp for p in plist), default=0.0)


def forecast_availability(
    avail_by_pos: dict[str, list[PoolPlayer]],
    priors: SlotPriors,
    upcoming: list[tuple[int, int, dict]],
    n_rollouts: int,
    seed: int,
    opponent_params=None,
) -> AvailabilityForecast:
    """Monte-Carlo survival + expected-best-vorp forecast at our next pick.

    `avail_by_pos` must already be the live availability view (each
    position's list pre-sorted per `opponent_pick`'s contract). Never
    mutates `avail_by_pos` or any `counts` dict inside `upcoming`.
    """
    tracked: dict[str, list[str]] = {
        pos: [p.ref for p in plist[:_SURVIVAL_WINDOW]]
        for pos, plist in avail_by_pos.items()
    }
    n_upcoming = len(upcoming)

    if n_upcoming == 0:
        survival = {ref: 1.0 for refs in tracked.values() for ref in refs}
        expected_best_vorp = {
            pos: _best_vorp(plist) for pos, plist in avail_by_pos.items()
        }
        return AvailabilityForecast(
            n_rollouts=n_rollouts,
            n_upcoming=0,
            survival=survival,
            expected_best_vorp=expected_best_vorp,
        )

    survive_counts = {ref: 0 for refs in tracked.values() for ref in refs}
    best_vorp_sums = {pos: 0.0 for pos in avail_by_pos}

    for k in range(n_rollouts):
        rng = np.random.default_rng(seed + k)
        taken: set = set()
        rollout_counts: dict[int, dict] = {}

        for franchise_slot, round_, counts in upcoming:
            seat_counts = rollout_counts.setdefault(franchise_slot, dict(counts))
            picks_left_after = ROUNDS - round_
            view = _avail_view(avail_by_pos, taken)
            pick = opponent_pick(
                view,
                priors,
                franchise_slot,
                round_,
                seat_counts,
                picks_left_after,
                rng,
                params=opponent_params,
            )
            taken.add(pick.ref)
            seat_counts[pick.position] = seat_counts.get(pick.position, 0) + 1

        for refs in tracked.values():
            for ref in refs:
                if ref not in taken:
                    survive_counts[ref] += 1

        final_view = _avail_view(avail_by_pos, taken)
        for pos in avail_by_pos:
            best_vorp_sums[pos] += _best_vorp(final_view.get(pos, []))

    survival = {ref: cnt / n_rollouts for ref, cnt in survive_counts.items()}
    expected_best_vorp = {pos: s / n_rollouts for pos, s in best_vorp_sums.items()}

    return AvailabilityForecast(
        n_rollouts=n_rollouts,
        n_upcoming=n_upcoming,
        survival=survival,
        expected_best_vorp=expected_best_vorp,
    )


def vona(
    avail_by_pos: dict[str, list[PoolPlayer]], forecast: AvailabilityForecast
) -> dict[str, float]:
    """How much value dies at each position if we wait one turn:
    best-available-now vorp - E[best-available-at-our-next-pick vorp].
    Non-negative up to Monte Carlo noise (the live-view-at-a-later-point
    is always a subset of the current view, so this holds exactly per
    rollout, hence exactly in expectation too)."""
    return {
        pos: _best_vorp(plist) - forecast.expected_best_vorp.get(pos, _best_vorp(plist))
        for pos, plist in avail_by_pos.items()
    }
