"""Tests for the opponent QB-timing measurement harness (Phase 4 Task 2)
and the QB need-scale fit (Task 4)."""
from simfixtures import synthetic_pool, synthetic_priors

from ffi.sim.calibrate import fit_qb_need_scale, measure_qb_timing

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


# --- Task 4: fit_qb_need_scale ------------------------------------------------

# LOW, roughly-flat QB priors: un-scaled, opponents take QB1 late. The knob's
# job is to pull QB1 timing forward to hit a historical target, so the fit must
# prefer the strongest 0-QB boost available in the grid.
_low_priors = synthetic_priors(qb_share_r1=0.15)
# Synthetic "historical" target: very early QB1 (round 1), keyed by opponent
# slot, same shape as `historical_qb_timing` returns (fit reads it via the
# seasons-weighted league means + per-slot QB1 MAE).
_historical_synth = {
    slot: {"qb1": 1.0, "qb2": 3.0, "qb3": 9.0, "seasons": 16.0} for slot in range(1, 12)
}


def test_fit_prefers_strong_boost_for_early_qb_target():
    best, trials = fit_qb_need_scale(
        pool,
        _low_priors,
        _historical_synth,
        n_drafts=10,
        base_seed=5,
        grid={"s0": (1.0, 6.0), "s1": (1.0,), "s2": (1.0,)},
    )
    # target QB1 is round 1; the 6.0 zero-QB boost pulls opponents onto QBs
    # far earlier than the un-scaled 1.0, so it must win.
    assert best.pos_need_scale == (("QB", (6.0, 1.0, 1.0)),)


def test_fit_trials_sorted_by_objective_ascending():
    _best, trials = fit_qb_need_scale(
        pool,
        _low_priors,
        _historical_synth,
        n_drafts=10,
        base_seed=5,
        grid={"s0": (1.0, 6.0), "s1": (1.0,), "s2": (1.0,)},
    )
    objectives = [t["objective"] for t in trials]
    assert objectives == sorted(objectives)
    # every candidate is recorded with its measured means
    assert len(trials) == 2
    assert all({"scale", "qb1", "qb2", "qb3", "objective"} <= set(t) for t in trials)
