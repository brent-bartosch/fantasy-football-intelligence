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

`survival` is populated only for the head `cand_window * 2` players of each
position in the CALLER's `avail_by_pos`, where `cand_window` is the EFFECTIVE
one (`opponent_params.cand_window` if given, else
`DEFAULT_OPPONENT_PARAMS.cand_window`) -- deeper players are, by
construction, never a plausible opponent target within one turn under that
window, so tracking their survival would bloat the dict for no signal. This
is derived fresh inside `forecast_availability` on every call (not a
module-level constant) so a caller overriding `cand_window` gets matching
survival coverage rather than silently truncated results.
`expected_best_vorp`, by contrast, is computed from each rollout's live
(post-simulated-picks) availability view directly, not restricted to the
tracked window -- this is both correct and simpler than special-casing the
window's edge.

Empty `upcoming` (a back-to-back snake turn, e.g. draft position 12's
round 1 -> round 2) short-circuits: nothing can be taken before our next
pick, so `survival` is 1.0 for every tracked player, `expected_best_vorp`
equals the current best, and `vona` is 0.0 everywhere.

A franchise slot may appear more than once in `upcoming` (e.g. draft
positions 1/12 pick twice back-to-back at a snake turn boundary). Since the
caller cannot know the outcome of its own earlier simulated pick, every
`upcoming` entry for the same slot must carry the SAME static `counts`
snapshot (whatever the caller currently knows to be true); the simulator
itself evolves a per-rollout working copy across that slot's repeat
appearances (mirroring `ffi.sim.draft.run_draft`'s own bookkeeping). A
mismatched later `counts` for an already-seen slot is a caller bug -- it
would mean two different claims about the same seat's roster at the same
point in time -- and raises `ValueError` naming the slot (fail-loud, not
silently discarded).
"""
from dataclasses import dataclass

import numpy as np

from ffi.sim.draft import ROUNDS, _avail_view
from ffi.sim.opponent import DEFAULT_OPPONENT_PARAMS, opponent_pick
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors


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
    mutates `avail_by_pos` or any `counts` dict inside `upcoming`. Raises
    `ValueError` if `n_rollouts <= 0`, or if the same franchise slot appears
    twice in `upcoming` with differing `counts` (see module docstring).
    """
    if n_rollouts <= 0:
        raise ValueError(f"n_rollouts must be positive, got {n_rollouts}")

    survival_window = (opponent_params or DEFAULT_OPPONENT_PARAMS).cand_window * 2
    tracked: dict[str, list[str]] = {
        pos: [p.ref for p in plist[:survival_window]]
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

    # Repeat-slot counts must be internally consistent (see module docstring)
    # -- validated once, up front, since it's a property of `upcoming` alone
    # and doesn't depend on any rollout's random draws.
    seed_counts: dict[int, dict] = {}
    for franchise_slot, _round, counts in upcoming:
        if franchise_slot in seed_counts:
            if counts != seed_counts[franchise_slot]:
                raise ValueError(
                    f"upcoming has inconsistent counts for franchise_slot "
                    f"{franchise_slot}: first occurrence had "
                    f"{seed_counts[franchise_slot]!r}, a later occurrence had "
                    f"{counts!r} -- repeat entries for the same slot must carry "
                    "the same static counts snapshot; the simulator evolves its "
                    "own per-rollout working copy across them"
                )
        else:
            seed_counts[franchise_slot] = counts

    survive_counts = {ref: 0 for refs in tracked.values() for ref in refs}
    best_vorp_sums = {pos: 0.0 for pos in avail_by_pos}

    for k in range(n_rollouts):
        rng = np.random.default_rng(seed + k)
        taken: set = set()
        rollout_counts: dict[int, dict] = {
            slot: dict(c) for slot, c in seed_counts.items()
        }

        for franchise_slot, round_, _counts in upcoming:
            seat_counts = rollout_counts[franchise_slot]
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
