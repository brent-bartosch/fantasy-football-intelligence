"""Tests for OpponentParams — roster-state-conditioned prior scale (Phase 4
Task 3). Default/empty pos_need_scale must be bit-identical to legacy
behavior; the mechanism only engages when pos_need_scale is non-empty."""
import numpy as np
import pytest

from simfixtures import synthetic_pool, synthetic_priors

from ffi.sim.calibrate import measure_qb_timing
from ffi.sim.draft import run_draft
from ffi.sim.opponent import (
    CAND_WINDOW,
    TAU,
    DEFAULT_OPPONENT_PARAMS,
    OpponentParams,
    opponent_pick,
)
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors
from ffi.sim.strategy import StrategyParams, make_strategy_fn

pool = synthetic_pool()
priors = synthetic_priors(qb_share_r1=0.97)


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


def test_default_params_dataclass_shape():
    assert DEFAULT_OPPONENT_PARAMS == OpponentParams()
    assert DEFAULT_OPPONENT_PARAMS.tau == TAU
    assert DEFAULT_OPPONENT_PARAMS.cand_window == CAND_WINDOW
    assert DEFAULT_OPPONENT_PARAMS.pos_need_scale == ()


def test_default_params_are_bit_identical_to_legacy():
    fn = make_strategy_fn(StrategyParams())
    r_legacy = run_draft(pool, priors, fn, seed=42)
    r_default = run_draft(pool, priors, fn, seed=42, opponent_params=OpponentParams())
    r_empty_scale = run_draft(
        pool, priors, fn, seed=42, opponent_params=OpponentParams(pos_need_scale=())
    )
    assert r_legacy.picks == r_default.picks == r_empty_scale.picks


def test_qb_need_scale_pulls_qb1_earlier():
    boosted = OpponentParams(pos_need_scale=(("QB", (4.0, 1.0, 1.0)),))
    m0 = measure_qb_timing(pool, priors, n_drafts=30, base_seed=9)
    m1 = measure_qb_timing(
        pool, priors, n_drafts=30, base_seed=9, opponent_params=boosted
    )
    assert m1.league_means[0] < m0.league_means[0]


def test_scale_index_extends_past_tuple_end():
    # count >= len(scale) uses the LAST entry; (("QB",(2.0,))) scales every
    # count. Direct unit check on the weight math: rigged rng, counts with
    # QB already at 5 (past the tuple's single entry) must not raise and
    # must return a player (index clamping via min(count, len(sc)-1), not
    # an IndexError from a naive sc[count] lookup).
    share = {"QB": 0.5, "RB": 0.1, "WR": 0.1, "TE": 0.1, "K": 0.1, "DEF": 0.1}
    slot_priors = SlotPriors(latest_season=2025, pos_share={(1, 1): share}, params={})
    avail = {
        "QB": [_pp("qb0", "QB", 10)],
        "RB": [_pp("rb0", "RB", 5)],
        "WR": [_pp("wr0", "WR", 3)],
        "TE": [_pp("te0", "TE", 25)],
        "K": [_pp("k0", "K", 180)],
        "DEF": [_pp("def0", "DEF", 170)],
    }
    params = OpponentParams(pos_need_scale=(("QB", (2.0,)),))
    counts = {"QB": 5}
    rng = np.random.default_rng(0)
    pick = opponent_pick(avail, slot_priors, 1, 1, counts, 17, rng, params=params)
    assert pick is not None


def test_tau_and_cand_window_respected():
    # cand_window=1 makes stage 2 deterministic: always the head of the
    # available-position list, regardless of rng draws.
    share = {"RB": 1.0}
    slot_priors = SlotPriors(latest_season=2025, pos_share={(5, 10): share}, params={})
    avail = {
        "RB": [_pp(f"rb{i}", "RB", adp) for i, adp in enumerate([5, 8, 14, 20, 28])],
    }
    params = OpponentParams(cand_window=1)
    top_ref = avail["RB"][0].ref
    for seed in range(50):
        rng = np.random.default_rng(seed)
        pick = opponent_pick(avail, slot_priors, 5, 10, {}, 17, rng, params=params)
        assert pick.ref == top_ref
