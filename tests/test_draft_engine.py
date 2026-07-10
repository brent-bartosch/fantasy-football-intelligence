"""Tests for the seeded snake draft engine (Phase 3 / Task 7)."""
from collections import Counter

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from ffi.sim.draft import (
    ROUNDS,
    TEAMS,
    TOTAL_PICKS,
    DraftResult,
    run_draft,
    snake_position,
)
from ffi.sim.opponent import feasible
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import POSITIONS, SlotPriors


# --------------------------------------------------------------------------
# snake_position
# --------------------------------------------------------------------------


def test_snake_order():
    assert snake_position(1) == (1, 1)
    assert snake_position(12) == (1, 12)
    assert snake_position(13) == (2, 12)
    assert snake_position(24) == (2, 1)
    # NOTE on deviation from the brief: the brief's illustrative test asserts
    # snake_position(228) == (19, 1). That's inconsistent with its own other
    # four examples: round 19 is odd, and odd rounds run ascending (round 1:
    # overall 1 -> position 1, overall 12 -> position 12), while even rounds
    # run descending (round 2: overall 13 -> position 12, overall 24 ->
    # position 1). Under that (the only rule consistent with all four other
    # examples), the LAST pick of round 19 -- overall 228, the 12th pick
    # within the round -- is position 12, not 1. (19, 1) is actually
    # snake_position(217), the FIRST pick of round 19 (which starts at
    # position 1, mirroring round 1, since both round numbers are odd).
    # Verified by hand and by direct computation. Using the corrected value.
    assert snake_position(217) == (19, 1)
    assert snake_position(228) == (19, 12)


def test_snake_position_covers_every_overall_exactly_once():
    seen = set()
    for overall in range(1, TOTAL_PICKS + 1):
        rnd, pos = snake_position(overall)
        assert 1 <= rnd <= ROUNDS
        assert 1 <= pos <= TEAMS
        seen.add((rnd, pos))
    assert len(seen) == TOTAL_PICKS  # every (round, position) hit exactly once


# --------------------------------------------------------------------------
# Toy fixtures
# --------------------------------------------------------------------------


def _toy_pool() -> list[PoolPlayer]:
    """~350 synthetic PoolPlayers spanning a realistic position mix and ADP
    ordering: RB/WR dominate the early ADP range, QB/TE spread through the
    middle, K/DEF cluster very late. The bottom slice of each position has
    no real ADP (Sleeper's undrafted-sentinel case, mapped to None)."""
    rng = np.random.default_rng(0)  # fixture-local; independent of any draft seed
    specs = [
        ("QB", 60, 8.0, 220.0),
        ("RB", 90, 3.0, 260.0),
        ("WR", 110, 2.0, 260.0),
        ("TE", 50, 15.0, 240.0),
        ("K", 25, 150.0, 300.0),
        ("DEF", 25, 150.0, 300.0),
    ]
    players = []
    for pos, n, adp_lo, adp_hi in specs:
        adps = np.linspace(adp_lo, adp_hi, n)
        noise = rng.normal(0, 3, n)
        n_real_adp = int(n * 0.85)  # bottom ~15% are undrafted-sentinel (adp=None)
        for i, adp in enumerate(adps):
            proj = max(1.0, 320.0 - adp * 0.9 + noise[i])
            vorp = proj - 60.0
            players.append(
                PoolPlayer(
                    ref=f"{pos}{i}",
                    name=f"{pos}{i}",
                    position=pos,
                    proj_points=float(proj),
                    vorp=float(vorp),
                    tier=1 + i // 10,
                    adp=float(adp) if i < n_real_adp else None,
                    gsis_id=None,
                )
            )
    assert 300 <= len(players) <= 400
    return players


def _toy_priors() -> SlotPriors:
    """Plausible shares for every (slot, round) in 1-12 x 1-19: RB/WR-heavy
    early, QB/TE mixed in mid-round, K/DEF pushed late."""
    pos_share = {}
    for slot in range(1, TEAMS + 1):
        for rnd in range(1, ROUNDS + 1):
            if rnd <= 3:
                share = {
                    "QB": 0.10,
                    "RB": 0.35,
                    "WR": 0.35,
                    "TE": 0.10,
                    "K": 0.05,
                    "DEF": 0.05,
                }
            elif rnd <= 8:
                share = {
                    "QB": 0.20,
                    "RB": 0.25,
                    "WR": 0.25,
                    "TE": 0.15,
                    "K": 0.075,
                    "DEF": 0.075,
                }
            elif rnd <= 15:
                share = {
                    "QB": 0.15,
                    "RB": 0.20,
                    "WR": 0.20,
                    "TE": 0.15,
                    "K": 0.15,
                    "DEF": 0.15,
                }
            else:
                share = {
                    "QB": 0.05,
                    "RB": 0.10,
                    "WR": 0.10,
                    "TE": 0.05,
                    "K": 0.35,
                    "DEF": 0.35,
                }
            pos_share[(slot, rnd)] = share
    return SlotPriors(latest_season=2025, pos_share=pos_share, params={})


def greedy_vorp_fn(avail_by_pos, round_, counts, picks_left_after):
    """Stand-in for Task 8's strategy fn: picks the single max-VORP player
    among positions that are both available and feasible."""
    best = None
    for pos in POSITIONS:
        cands = avail_by_pos.get(pos) or []
        if not cands or not feasible(counts, pos, picks_left_after):
            continue
        top = max(cands, key=lambda p: p.vorp)
        if best is None or top.vorp > best.vorp:
            best = top
    if best is None:
        raise ValueError(
            f"greedy_vorp_fn: no feasible position available (counts={counts}, "
            f"picks_left_after={picks_left_after})"
        )
    return best


@pytest.fixture()
def toy_pool():
    return _toy_pool()


@pytest.fixture()
def toy_priors():
    return _toy_priors()


# --------------------------------------------------------------------------
# run_draft: determinism, pinning, legality of mechanics
# --------------------------------------------------------------------------


def test_draft_is_deterministic_by_seed(toy_pool, toy_priors):
    r1 = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=123)
    r2 = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=123)
    assert r1.picks == r2.picks
    assert r1.slot_of_position == r2.slot_of_position
    assert r1.our_position == r2.our_position


def test_different_seeds_diverge(toy_pool, toy_priors):
    r1 = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=1)
    r2 = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=2)
    assert r1.picks != r2.picks


def test_our_position_pinning(toy_pool, toy_priors):
    res = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=7, our_position=5)
    assert res.our_position == 5
    assert res.slot_of_position[5] == 12  # default our_franchise_slot

    expected_overalls = [
        overall
        for overall in range(1, TOTAL_PICKS + 1)
        if snake_position(overall)[1] == 5
    ]
    assert expected_overalls[:3] == [5, 20, 29]  # "5th, 20th, ..." per the brief

    actual_overalls = [p["overall"] for p in res.picks if p["position_slot"] == 5]
    assert actual_overalls == expected_overalls
    for p in res.picks:
        if p["position_slot"] == 5:
            assert p["franchise_slot"] == 12
    assert len(res.rosters[5]) == ROUNDS


def test_our_position_none_still_resolves_and_participates(toy_pool, toy_priors):
    res = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=3, our_franchise_slot=12)
    assert 1 <= res.our_position <= TEAMS
    assert res.slot_of_position[res.our_position] == 12
    our_picks = [p for p in res.picks if p["position_slot"] == res.our_position]
    assert len(our_picks) == ROUNDS


def test_no_player_drafted_twice(toy_pool, toy_priors):
    res = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=42)
    all_refs = [p["ref"] for p in res.picks]
    assert len(all_refs) == TOTAL_PICKS
    assert len(set(all_refs)) == TOTAL_PICKS  # no dupes


def test_picks_list_shape_and_rosters_consistency(toy_pool, toy_priors):
    res = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=11)
    assert isinstance(res, DraftResult)
    assert len(res.picks) == TOTAL_PICKS
    for p in res.picks:
        assert set(p.keys()) == {
            "overall",
            "position_slot",
            "franchise_slot",
            "pos",
            "ref",
            "name",
        }
    for pos in range(1, TEAMS + 1):
        assert len(res.rosters[pos]) == ROUNDS
    # slot_of_position is a bijection over 1-12
    assert sorted(res.slot_of_position.keys()) == list(range(1, TEAMS + 1))
    assert sorted(res.slot_of_position.values()) == list(range(1, TEAMS + 1))


def test_franchise_slot_permutation_matches_priors_usage(toy_pool, toy_priors):
    # Every opponent pick's recorded franchise_slot should equal
    # slot_of_position[position_slot] (sanity on the permutation bookkeeping).
    res = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=55)
    for p in res.picks:
        assert p["franchise_slot"] == res.slot_of_position[p["position_slot"]]


def test_our_pick_fn_infeasible_pick_raises(toy_pool, toy_priors):
    # Strategy fn that always tries to take a QB, even once QB is at its
    # starter cap and infeasible near the end of the draft -- must raise,
    # not silently corrupt the roster.
    def bad_fn(avail_by_pos, round_, counts, picks_left_after):
        return avail_by_pos["QB"][0]

    with pytest.raises(ValueError):
        run_draft(toy_pool, toy_priors, bad_fn, seed=1, our_position=1)


def test_our_pick_fn_already_taken_pick_raises(toy_pool, toy_priors):
    # Strategy fn that repeats its first-ever pick forever -- the second call
    # returns an already-drafted player and must raise.
    state = {"first": None}

    def repeat_fn(avail_by_pos, round_, counts, picks_left_after):
        if state["first"] is None:
            pick = max(avail_by_pos["RB"], key=lambda p: p.vorp)
            state["first"] = pick
            return pick
        return state["first"]

    with pytest.raises(ValueError):
        run_draft(toy_pool, toy_priors, repeat_fn, seed=1, our_position=1)


# --------------------------------------------------------------------------
# Hypothesis property: every roster produced is legal
# --------------------------------------------------------------------------


@given(seed=st.integers(0, 10_000))
@settings(max_examples=50, deadline=None)
def test_every_roster_is_legal(seed):
    pool = _toy_pool()
    priors = _toy_priors()
    res = run_draft(pool, priors, greedy_vorp_fn, seed=seed)
    for pos_slot, roster in res.rosters.items():
        counts = Counter(p.position for p in roster)
        assert len(roster) == ROUNDS
        assert counts["QB"] >= 2 and counts["RB"] >= 2 and counts["WR"] >= 3
        assert counts["TE"] >= 1 and counts["K"] >= 1 and counts["DEF"] >= 1
        flex_surplus = (
            max(0, counts["RB"] - 2)
            + max(0, counts["WR"] - 3)
            + max(0, counts["TE"] - 1)
        )
        assert flex_surplus >= 1
