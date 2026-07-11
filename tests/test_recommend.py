"""Tests for the draft assistant recommendation engine (Phase 4 / Task 12).

The load-bearing property is `test_primary_equals_strategy_fn_property`:
`recommend(...).primary` must equal `make_strategy_fn(params)(...)` for
identical inputs, always -- the assistant's #1 answer IS the rehearsed sim
strategy, never a second implementation. Everything else here tests the
additive context (`rule`, `top`, `by_position`, `vona`, `notes`) built around
that pinned pick.
"""
import pytest
from simfixtures import synthetic_pool, synthetic_priors

from ffi.draft.recommend import MATERIAL_VONA, recommend
from ffi.sim.availability import AvailabilityForecast
from ffi.sim.draft import run_draft
from ffi.sim.pool import PoolPlayer
from ffi.sim.strategy import (
    StrategyParams,
    _pick_best,
    make_strategy_fn,
    rule4_candidates,
)


def _pp(ref, position, vorp=0.0, tier=1, adp=None, proj=100.0):
    return PoolPlayer(
        ref=ref,
        name=ref,
        position=position,
        proj_points=proj,
        vorp=vorp,
        tier=tier,
        adp=adp,
        gsis_id=None,
    )


def _avail(**by_pos):
    base = {p: [] for p in ("QB", "RB", "WR", "TE", "K", "DEF")}
    base.update(by_pos)
    return base


# ---------------------------------------------------------------------------
# The consistency contract.
# ---------------------------------------------------------------------------


def _capture_states(seeds, params):
    """Run full seeded drafts with the real strategy fn wrapped in a
    recording `PickFn`, capturing (avail_by_pos, round_, counts,
    picks_left_after) at every our-seat turn. `counts` is copied at capture
    time since `run_draft` mutates the same dict object in place across the
    draft -- a bare reference would show every state as the final roster."""
    pool = synthetic_pool()
    priors = synthetic_priors()
    strategy_fn = make_strategy_fn(params)
    states = []

    def recording_fn(avail_by_pos, round_, counts, picks_left_after):
        states.append((avail_by_pos, round_, dict(counts), picks_left_after))
        return strategy_fn(avail_by_pos, round_, counts, picks_left_after)

    for seed in seeds:
        run_draft(pool, priors, recording_fn, seed=seed, our_position=6)
    return states


def test_primary_equals_strategy_fn_property():
    params = StrategyParams()
    strategy_fn = make_strategy_fn(params)
    # 11 seeds x 19 our-seat turns/draft = 209 >= 200 legal states.
    states = _capture_states(range(11), params)
    assert len(states) >= 200

    for avail_by_pos, round_, counts, picks_left_after in states[:200]:
        expected = strategy_fn(avail_by_pos, round_, counts, picks_left_after)
        rec = recommend(avail_by_pos, round_, counts, picks_left_after, params)
        assert rec.primary.ref == expected.ref, (
            f"round={round_} counts={counts} picks_left_after={picks_left_after}: "
            f"recommend primary {rec.primary.ref!r} != strategy_fn {expected.ref!r}"
        )


# ---------------------------------------------------------------------------
# Rule attribution.
# ---------------------------------------------------------------------------


def test_rule_attribution():
    # qb_deadline: round 2, 0 QBs, default plan (2,5,9) -> forced.
    avail = _avail(
        QB=[_pp("qb1", "QB", vorp=1.0)],
        RB=[_pp("rb1", "RB", vorp=50.0)],
    )
    rec = recommend(avail, 2, {}, 17, StrategyParams())
    assert rec.rule == "qb_deadline"
    assert rec.primary.ref == "qb1"

    # defk: round >= defk_round, no DEF held -> forced.
    avail = _avail(
        DEF=[_pp("def1", "DEF", vorp=5.0)],
        RB=[_pp("rb1", "RB", vorp=99.0)],
    )
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}
    rec = recommend(avail, 14, counts, 5, StrategyParams())
    assert rec.rule == "defk"
    assert rec.primary.position == "DEF"

    # feasibility: required_picks == picks_left_after -> restricted to unmet.
    counts = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    avail = _avail(
        QB=[_pp("qbHuge", "QB", vorp=999.0)],
        DEF=[_pp("def1", "DEF", vorp=5.0)],
        K=[_pp("k1", "K", vorp=50.0)],
    )
    rec = recommend(avail, 12, counts, 3, StrategyParams())
    assert rec.rule == "feasibility"
    assert rec.primary.position == "K"

    # value: no force conditions met -> plain argmax.
    avail = _avail(RB=[_pp("rb1", "RB", vorp=10.0)])
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    rec = recommend(avail, 6, counts, 12, StrategyParams())
    assert rec.rule == "value"
    assert rec.primary.ref == "rb1"


# ---------------------------------------------------------------------------
# top / by_position.
# ---------------------------------------------------------------------------


def test_top_is_desc_and_tiebroken_like_pick_best():
    avail = _avail(
        RB=[
            _pp("rbA", "RB", vorp=10.0, adp=50.0),
            _pp("rbB", "RB", vorp=20.0, adp=10.0),
        ],
        WR=[_pp("wrA", "WR", vorp=15.0, adp=30.0)],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    params = StrategyParams()
    rec = recommend(avail, 6, counts, 12, params)

    scores = [s for s, _ in rec.top]
    assert scores == sorted(scores, reverse=True)

    scored = rule4_candidates(avail, 6, counts, 12, params)
    assert rec.top[0][1].ref == _pick_best(scored).ref
    assert rec.by_position["RB"][0].ref == "rbB"  # highest vorp RB first


# ---------------------------------------------------------------------------
# VONA.
# ---------------------------------------------------------------------------


def test_vona_none_without_forecast():
    avail = _avail(RB=[_pp("rb1", "RB", vorp=10.0)])
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    rec = recommend(avail, 6, counts, 12, StrategyParams(), forecast=None)
    assert rec.vona is None


def test_vona_present_and_notes_when_forecast_given():
    avail = _avail(
        QB=[_pp("qb1", "QB", vorp=10.0)],
        RB=[_pp("rb1", "RB", vorp=8.0)],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    forecast = AvailabilityForecast(
        n_rollouts=10,
        n_upcoming=1,
        survival={},
        expected_best_vorp={"QB": 5.0, "RB": 7.9},  # QB drops 5.0 (material)
    )  # RB drops 0.1 (below MATERIAL_VONA)
    rec = recommend(avail, 6, counts, 12, StrategyParams(), forecast=forecast)

    assert rec.vona == pytest.approx(
        {
            "QB": 5.0,
            "RB": 0.1,
            "WR": 0.0,
            "TE": 0.0,
            "K": 0.0,
            "DEF": 0.0,
        }
    )
    assert any("QB" in n and "vorp" in n for n in rec.notes)
    assert not any("RB" in n and "vorp" in n for n in rec.notes)
    assert MATERIAL_VONA == 1.0


# ---------------------------------------------------------------------------
# Notes: last-in-tier.
# ---------------------------------------------------------------------------


def test_last_in_tier_note_fires():
    avail = _avail(
        RB=[
            _pp("rbA", "RB", vorp=10.0, tier=1),
            _pp("rbB", "RB", vorp=9.0, tier=1),
        ],
        QB=[_pp("qbAlone", "QB", vorp=5.0, tier=2)],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    rec = recommend(avail, 6, counts, 12, StrategyParams())

    assert any("last tier-2 QB" in n for n in rec.notes)
    assert not any("RB" in n and "last tier" in n for n in rec.notes)
