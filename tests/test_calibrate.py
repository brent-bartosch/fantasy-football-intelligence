"""Tests for the opponent QB-timing measurement harness (Phase 4 Task 2)."""
from simfixtures import synthetic_pool, synthetic_priors

from ffi.sim.calibrate import measure_qb_timing

pool = synthetic_pool()
priors = synthetic_priors(qb_share_r1=0.97)


def test_measure_qb_timing_qb_heavy_priors_yield_early_qb1():
    m = measure_qb_timing(pool, priors, n_drafts=30, base_seed=7)
    assert m.n_drafts == 30
    assert m.league_means[0] < 1.6  # nearly every opponent takes QB1 in R1
    # NOTE on deviation from the brief: the brief's illustrative assertion
    # reads `set(m.per_slot) == set(range(1, 13)) - set()` ("all 12 slots
    # present"), which is inconsistent with its own Step 2 spec ("for every
    # pick with ... franchise_slot != 12") and with
    # `test_our_seat_excluded` below (both explicitly exclude franchise slot
    # 12, our own seat, from `per_slot`). 11 opponent slots is the
    # mathematically consistent reading; using it here.
    assert set(m.per_slot) == set(range(1, 13)) - {12}  # 11 opponent slots
    assert m.league_means[0] <= m.league_means[1] <= m.league_means[2]


def test_measure_is_deterministic():
    a = measure_qb_timing(pool, priors, n_drafts=10, base_seed=3)
    b = measure_qb_timing(pool, priors, n_drafts=10, base_seed=3)
    assert a == b


def test_our_seat_excluded():
    # our seat (franchise slot 12) must not contribute to opponent stats:
    # per_slot[12] stats come only from drafts where slot 12 is NOT our seat — with
    # our_franchise_slot fixed at 12 in measure_qb_timing, slot 12 must be absent.
    m = measure_qb_timing(pool, priors, n_drafts=10, base_seed=3)
    assert 12 not in m.per_slot
