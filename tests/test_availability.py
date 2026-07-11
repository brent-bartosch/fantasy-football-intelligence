"""Tests for the VONA availability layer (Phase 4 / Task 8).

Monte-Carlo forward-simulation of the calibrated opponent model
(`ffi.sim.opponent.opponent_pick`) across the picks between now and our next
turn. Pure function of its arguments (no DB, no I/O) -- this sits on the
draft assistant's between-picks path.
"""
import copy
import time

import pytest

from simfixtures import synthetic_pool, synthetic_priors

from ffi.sim.availability import forecast_availability, vona
from ffi.sim.draft import _build_sorted_pool
from ffi.sim.opponent import OpponentParams


@pytest.fixture()
def avail():
    return _build_sorted_pool(synthetic_pool())


def test_certain_taken_player_has_low_survival(avail):
    # Priors force QB with prob ~1 in round 1; cand_window=1 forces stage 2
    # to always take the single best-available QB. One upcoming pick, one
    # simulated opponent (franchise slot 3, round 1, no prior picks) --
    # the head QB should almost never survive; everyone else always does.
    priors = synthetic_priors(qb_share_r1=0.999999)
    params = OpponentParams(cand_window=1)
    upcoming = [(3, 1, {})]

    f = forecast_availability(
        avail, priors, upcoming, n_rollouts=200, seed=1, opponent_params=params
    )

    head_qb_ref = avail["QB"][0].ref
    assert f.survival[head_qb_ref] < 0.05

    other_qb_ref = avail["QB"][1].ref
    assert f.survival[other_qb_ref] > 0.95
    rb_ref = avail["RB"][0].ref
    assert f.survival[rb_ref] > 0.95


def test_back_to_back_turn_is_identity(avail):
    priors = synthetic_priors()
    f = forecast_availability(avail, priors, upcoming=[], n_rollouts=50, seed=1)

    assert f.n_upcoming == 0
    assert all(v == 1.0 for v in f.survival.values())

    expected_best = {pos: max(p.vorp for p in plist) for pos, plist in avail.items()}
    assert f.expected_best_vorp == expected_best
    assert vona(avail, f) == {pos: 0.0 for pos in avail}


def test_deterministic_by_seed(avail):
    priors = synthetic_priors()
    upcoming = [(slot, 1, {}) for slot in range(1, 12)]

    f1 = forecast_availability(avail, priors, upcoming, n_rollouts=30, seed=7)
    f2 = forecast_availability(avail, priors, upcoming, n_rollouts=30, seed=7)
    assert f1.survival == f2.survival
    assert f1.expected_best_vorp == f2.expected_best_vorp

    f3 = forecast_availability(avail, priors, upcoming, n_rollouts=30, seed=8)
    assert f1.survival != f3.survival or f1.expected_best_vorp != f3.expected_best_vorp


def test_vona_nonnegative_up_to_noise(avail):
    priors = synthetic_priors()
    upcoming = [(slot, 1, {}) for slot in range(1, 12)]
    f = forecast_availability(avail, priors, upcoming, n_rollouts=100, seed=3)

    v = vona(avail, f)
    for pos in avail:
        assert v[pos] >= -1e-9, f"{pos} vona went negative: {v[pos]}"


def test_caller_state_not_mutated(avail):
    priors = synthetic_priors()
    upcoming = [(slot, 1, {"QB": 0}) for slot in range(1, 12)]
    avail_before = copy.deepcopy(avail)
    upcoming_before = copy.deepcopy(upcoming)

    forecast_availability(avail, priors, upcoming, n_rollouts=20, seed=5)

    assert [p.ref for plist in avail.values() for p in plist] == [
        p.ref for plist in avail_before.values() for p in plist
    ]
    assert upcoming == upcoming_before


def test_perf_budget(avail):
    priors = synthetic_priors()
    upcoming = [(((slot - 1) % 12) + 1, 1, {}) for slot in range(1, 23)]

    start = time.perf_counter()
    forecast_availability(avail, priors, upcoming, n_rollouts=200, seed=11)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"forecast_availability took {elapsed:.2f}s, budget 2.0s"
