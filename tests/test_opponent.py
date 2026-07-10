import numpy as np
import pytest

from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors
from ffi.sim.opponent import (
    CAND_WINDOW,
    ROSTER_DAMP,
    STARTERS,
    TAU,
    feasible,
    opponent_pick,
    required_picks,
)


def _pp(ref, position, adp, proj=100.0):
    return PoolPlayer(
        ref=ref,
        name=ref,
        position=position,
        proj_points=proj,
        vorp=0.0,
        tier=1,
        adp=adp,
        gsis_id=None,
    )


def _slot_priors(share, slot, round_):
    """Minimal SlotPriors with a single (slot, round) entry — no DB needed."""
    return SlotPriors(latest_season=2025, pos_share={(slot, round_): share}, params={})


# Candidate pool for the determinism test: sorted ascending by ADP within
# each position, per the avail_by_pos contract opponent_pick documents.
AVAIL = {
    "QB": [_pp(f"qb{i}", "QB", adp) for i, adp in enumerate([12, 18, 30, 45, 60])],
    "RB": [
        _pp(f"rb{i}", "RB", adp) for i, adp in enumerate([5, 8, 14, 20, 28, 40, 55, 70])
    ],
    "WR": [
        _pp(f"wr{i}", "WR", adp) for i, adp in enumerate([3, 9, 16, 22, 33, 48, 62, 80])
    ],
    "TE": [_pp(f"te{i}", "TE", adp) for i, adp in enumerate([25, 40, 65, 90])],
    "K": [_pp(f"k{i}", "K", adp) for i, adp in enumerate([180, 200, 210])],
    "DEF": [_pp(f"def{i}", "DEF", adp) for i, adp in enumerate([170, 190, 205])],
}

PRIORS = _slot_priors(
    {"QB": 0.15, "RB": 0.25, "WR": 0.25, "TE": 0.15, "K": 0.10, "DEF": 0.10}, 3, 2
)


def test_module_constants_match_interface():
    assert TAU == 1.8
    assert CAND_WINDOW == 12
    assert ROSTER_DAMP["QB"] == {3: 0.15, 4: 0.0}
    assert ROSTER_DAMP["TE"] == {2: 0.3, 4: 0.0}
    assert ROSTER_DAMP["K"] == {1: 0.02, 2: 0.0}
    assert ROSTER_DAMP["DEF"] == {1: 0.02, 2: 0.0}
    assert STARTERS == {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}


def test_required_picks_counts_flex():
    assert required_picks({}) == 11  # 2+2+3+1+1+1 starters + flex
    assert (
        required_picks({"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}) == 1
    )  # flex open
    assert (
        required_picks({"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 1, "DEF": 1}) == 0
    )  # flex covered


def test_feasibility_forces_starters_at_the_death():
    # 8 picks, no K/DEF, flex open (RB/WR/TE all at their starter cap, not over).
    # required_picks(counts) == 3 (K + DEF + flex), matching the brief's comment.
    counts = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    assert required_picks(counts) == 3

    # NOTE on deviation from the brief: the brief's illustrative test asserted
    # `not feasible(counts, "WR", picks_left_after=2)`. That's inconsistent with
    # the brief's own required_picks/feasible formulas: WR is flex-eligible, so
    # a 4th WR satisfies the open flex slot exactly as a 4th RB or 2nd TE would
    # (required_picks(c2) == 2 <= 2 → feasible). Verified this by hand and by
    # running the brief's pseudocode directly. QB is the correct "infeasible"
    # example here: QB is already at its starter cap (2/2) and is NOT
    # flex-eligible, so a 3rd QB does nothing to close the K/DEF/flex gap
    # (required_picks(c2) == 3 > 2 → infeasible). Swapped WR -> QB below.
    assert not feasible(counts, "QB", picks_left_after=2)
    assert feasible(counts, "K", picks_left_after=2)
    assert feasible(counts, "DEF", picks_left_after=2)
    # RB/WR/TE all still feasible too — each resolves the flex slot.
    assert feasible(counts, "RB", picks_left_after=2)
    assert feasible(counts, "WR", picks_left_after=2)
    assert feasible(counts, "TE", picks_left_after=2)


def test_pick_is_deterministic_given_seed():
    rng1, rng2 = np.random.default_rng(7), np.random.default_rng(7)
    p1 = opponent_pick(AVAIL, PRIORS, 3, 2, {}, 17, rng1)
    p2 = opponent_pick(AVAIL, PRIORS, 3, 2, {}, 17, rng2)
    assert p1.ref == p2.ref


def test_roster_damp_suppresses_fourth_qb():
    # QB has the single highest per-position share (0.20, vs 1/6 ~= 0.167
    # uniform baseline) — but with 3 QBs already rostered, ROSTER_DAMP hits
    # the >=3 threshold (0.15x). picks_left_after=15 keeps every position
    # feasible so only the priors+damp mechanics are under test.
    share = {"QB": 0.20, "RB": 0.25, "WR": 0.25, "TE": 0.15, "K": 0.10, "DEF": 0.05}
    priors = _slot_priors(share, 1, 1)
    counts = {"QB": 3}
    rng = np.random.default_rng(42)
    picks = [
        opponent_pick(AVAIL, priors, 1, 1, counts, 15, rng).position
        for _ in range(2000)
    ]
    qb_rate = picks.count("QB") / len(picks)
    assert qb_rate < 0.05, f"QB picked {qb_rate:.3%} of draws, expected < 5%"


def test_softmax_prefers_top_of_position_board():
    # Force position choice to RB deterministically (only RB has nonzero
    # prior share), then check the top-of-board RB (lowest ADP == AVAIL["RB"][0])
    # is the single most-frequently returned player over many draws.
    priors = _slot_priors({"RB": 1.0}, 5, 10)
    rng = np.random.default_rng(99)
    picks = [opponent_pick(AVAIL, priors, 5, 10, {}, 17, rng).ref for _ in range(2000)]
    from collections import Counter

    counts = Counter(picks)
    top_ref = AVAIL["RB"][0].ref
    assert counts.most_common(1)[0][0] == top_ref
    # and it should dominate, not just barely lead
    assert counts[top_ref] / len(picks) > 0.3


def test_never_returns_infeasible_position():
    # Forced endgame: QB is infeasible (see test_feasibility_forces_starters_at_the_death).
    counts = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    share = {"QB": 0.5, "RB": 0.15, "WR": 0.15, "TE": 0.1, "K": 0.05, "DEF": 0.05}
    priors = _slot_priors(share, 2, 18)
    for seed in range(300):
        rng = np.random.default_rng(seed)
        pick = opponent_pick(AVAIL, priors, 2, 18, counts, 2, rng)
        assert pick.position != "QB", f"seed {seed} returned infeasible QB pick"


def test_raises_when_no_feasible_position():
    # Last pick of the draft (picks_left_after=0), K and DEF both still
    # unfilled, flex already covered by a 3rd RB: required_picks(counts) == 2
    # (need both K and DEF), but only one pick remains, so no single position
    # can bring required_picks(c2) down to 0 — every position is infeasible.
    counts = {"QB": 2, "RB": 3, "WR": 3, "TE": 1}
    assert required_picks(counts) == 2
    share = {"QB": 1.0}
    priors = _slot_priors(share, 6, 19)
    rng = np.random.default_rng(1)
    with pytest.raises(ValueError):
        opponent_pick(AVAIL, priors, 6, 19, counts, 0, rng)
