"""Tests for the `qb_tier_targets` strategy knob (Phase 4 Task 6): a
rule-4-only *which-QB* filter (by `PoolPlayer.tier`), distinct from
`qb_not_before` (timing) and `qb_by_round` (deadline backstop). Mirrors
`test_strategy.py`'s conventions (`_pp`/`_avail` helpers, direct
`make_strategy_fn` calls -- no `run_draft` needed to isolate rule 4).
"""
from ffi.sim.pool import PoolPlayer
from ffi.sim.strategy import StrategyParams, make_strategy_fn


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


def test_tier_target_filters_rule4_qb_candidates():
    # qb_tier_targets=(2, ...): with counts["QB"]=0, QB #1 is capped at tier
    # <= 2. Only a tier-3 QB is on the board (huge vorp, would otherwise
    # dominate rule 4's argmax) -> it must be filtered out, leaving RB.
    avail = _avail(
        QB=[_pp("qbTier3", "QB", vorp=999.0, tier=3)],
        RB=[_pp("rb1", "RB", vorp=10.0, tier=1)],
    )
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    # qb_not_before default (1,1,1) doesn't block round 1; push qb_by_round
    # out so rule 2's deadline never fires and rule 4 is isolated.
    params = StrategyParams(qb_by_round=(10, 15, 20), qb_tier_targets=(2, 3, 99))
    fn = make_strategy_fn(params)
    pick = fn(avail, 1, counts, 17)
    assert pick.position != "QB"
    assert pick.ref == "rb1"


def test_tier_target_allows_qb_within_target_tier():
    # Same setup, but a tier-2 QB is ALSO on the board and dominates RB's
    # vorp -- it passes the tier<=2 filter, so rule 4 must take it.
    avail = _avail(
        QB=[
            _pp("qbTier3", "QB", vorp=999.0, tier=3),
            _pp("qbTier2", "QB", vorp=50.0, tier=2),
        ],
        RB=[_pp("rb1", "RB", vorp=10.0, tier=1)],
    )
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    params = StrategyParams(qb_by_round=(10, 15, 20), qb_tier_targets=(2, 3, 99))
    fn = make_strategy_fn(params)
    pick = fn(avail, 1, counts, 17)
    assert pick.position == "QB"
    assert pick.ref == "qbTier2"


def test_deadline_force_ignores_tier_target():
    # qb_by_round=(1,...) + qb_tier_targets=(1,...) + only a tier-3 QB left
    # (the tier-1 QB is already gone) -- round 1's deadline (rule 2) still
    # forces the best available QB regardless of tier; qb_tier_targets is
    # rule-4-only, exactly like qb_not_before.
    avail = _avail(
        QB=[_pp("qbTier3", "QB", vorp=5.0, tier=3)],
        RB=[_pp("rb1", "RB", vorp=99.0, tier=1)],
    )
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    params = StrategyParams(qb_by_round=(1, 4, 9), qb_tier_targets=(1, 1, 1))
    fn = make_strategy_fn(params)
    pick = fn(avail, 1, counts, 17)
    assert pick.position == "QB"
    assert pick.ref == "qbTier3"


def test_empty_targets_is_noop():
    # StrategyParams() vs StrategyParams(qb_tier_targets=()) -> identical
    # picks, seed-for-seed (same candidate board, same round/counts).
    avail = _avail(
        QB=[_pp("qbTier3", "QB", vorp=999.0, tier=3)],
        RB=[_pp("rb1", "RB", vorp=10.0, tier=1)],
    )
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    fn_default = make_strategy_fn(StrategyParams())
    fn_explicit_empty = make_strategy_fn(StrategyParams(qb_tier_targets=()))
    pick_default = fn_default(avail, 1, dict(counts), 17)
    pick_explicit = fn_explicit_empty(avail, 1, dict(counts), 17)
    assert pick_default.ref == pick_explicit.ref == "qbTier3"


def test_index_past_end_unrestricted():
    # counts["QB"]=2 with qb_tier_targets=(2, 3) (len 2) -> QB #3's index (2)
    # is past the tuple end -> unrestricted, so a tier-5 QB is still takeable
    # by rule 4's argmax.
    avail = _avail(
        QB=[_pp("qbTier5", "QB", vorp=999.0, tier=5)],
        RB=[_pp("rb1", "RB", vorp=10.0, tier=1)],
    )
    counts = {"QB": 2, "RB": 0, "WR": 0, "TE": 0}
    params = StrategyParams(
        qb_by_round=(10, 15, 20),
        qb_not_before=(1, 1, 1, 1),
        qb_tier_targets=(2, 3),
    )
    fn = make_strategy_fn(params)
    pick = fn(avail, 1, counts, 17)
    assert pick.position == "QB"
    assert pick.ref == "qbTier5"
