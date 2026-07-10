"""Tests for our-seat strategy logic (Phase 3 / Task 8)."""
import pytest

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
    """Build an avail_by_pos dict; positions omitted default to empty."""
    base = {p: [] for p in ("QB", "RB", "WR", "TE", "K", "DEF")}
    base.update(by_pos)
    return base


def test_default_params():
    p = StrategyParams()
    assert p.scenario == "qb_hoard_12"
    assert p.qb_by_round == (2, 5, 9)
    assert p.defk_round == 14
    assert p.caps == (("QB", 4), ("RB", 9), ("WR", 9), ("TE", 3), ("K", 2), ("DEF", 2))
    assert p.tier_break_bonus == 0.0
    assert p.qb_not_before == (1, 1, 1)


def test_qb_deadline_forces_qb():
    # Round 2, 0 QBs, default plan (2, 5, 9) -> the #1 QB deadline (round 2)
    # has arrived, so a QB is forced even though RB has vastly higher vorp.
    avail = _avail(
        QB=[_pp("qb1", "QB", vorp=1.0)],
        RB=[_pp("rb1", "RB", vorp=50.0)],
        WR=[_pp("wr1", "WR", vorp=40.0)],
    )
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 2, {}, 17)
    assert pick.position == "QB"
    assert pick.ref == "qb1"


def test_qb_deadline_smallest_unmet_n():
    # 1 QB held, plan wants #2 by round 5. At round 5 the deadline for n=2
    # has arrived -> forced, even though round 9's n=3 deadline hasn't.
    avail = _avail(
        QB=[_pp("qb2", "QB", vorp=1.0)],
        RB=[_pp("rb1", "RB", vorp=99.0)],
    )
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 5, {"QB": 1}, 14)
    assert pick.position == "QB"


# ---------------------------------------------------------------------------
# qb_not_before delay knob (Task 11 plan amendment): rule-4-only, doesn't
# affect rule 2's deadline force.
# ---------------------------------------------------------------------------


def _qb_top_vorp_avail():
    """QB is the argmax by vorp everywhere else on the board -- if rule 4
    ever considers QB, it wins."""
    return _avail(
        QB=[_pp("qbTop", "QB", vorp=100.0)],
        RB=[_pp("rb1", "RB", vorp=10.0)],
        WR=[_pp("wr1", "WR", vorp=9.0)],
    )


def test_qb_not_before_default_unchanged_qb_still_taken_round_1():
    # Default qb_not_before=(1, 1, 1) never blocks round 1 -- QB is still the
    # argmax pick round 1, same as before this knob existed.
    avail = _qb_top_vorp_avail()
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 1, {}, 17)
    assert pick.position == "QB"
    assert pick.ref == "qbTop"


def test_qb_not_before_delays_argmax_qb_until_its_round():
    # qb_by_round pushed well past round 3 so rule 2's deadline never fires
    # here -- isolates rule 4's qb_not_before filter. Rounds 1-2 must NOT
    # take the (otherwise dominant) QB; round 3 must.
    avail = _qb_top_vorp_avail()
    params = StrategyParams(qb_by_round=(10, 15, 20), qb_not_before=(3, 6, 10))
    fn = make_strategy_fn(params)

    pick_r1 = fn(avail, 1, {}, 17)
    assert pick_r1.position != "QB"

    pick_r2 = fn(avail, 2, {}, 17)
    assert pick_r2.position != "QB"

    pick_r3 = fn(avail, 3, {}, 17)
    assert pick_r3.position == "QB"
    assert pick_r3.ref == "qbTop"


def test_qb_by_round_deadline_still_wins_over_qb_not_before():
    # qb_not_before says "not before round 5" but qb_by_round's deadline
    # forces QB #1 at round 3 anyway -- rule 2 fires before rule 4 is ever
    # consulted, so the delay knob can't block a deadline force. Intended
    # fail-safe for a misconfigured (deadline < not_before) pair.
    avail = _avail(
        QB=[_pp("qb1", "QB", vorp=1.0)],
        RB=[_pp("rb1", "RB", vorp=99.0)],
    )
    params = StrategyParams(qb_by_round=(3, 6, 10), qb_not_before=(5, 8, 12))
    fn = make_strategy_fn(params)
    pick = fn(avail, 3, {}, 17)
    assert pick.position == "QB"
    assert pick.ref == "qb1"


def test_qb_not_before_shorter_than_qb_by_round_raises():
    params = StrategyParams(qb_by_round=(2, 5, 9), qb_not_before=(1, 1))
    with pytest.raises(ValueError, match="qb_not_before"):
        make_strategy_fn(params)


def test_defk_window_respected_not_before_deadline():
    # Round 10 (< defk_round=14): DEF/K must never be forced or chosen, even
    # with monster vorp, and even though counts holds none of either.
    avail = _avail(
        DEF=[_pp("def1", "DEF", vorp=200.0)],
        K=[_pp("k1", "K", vorp=200.0)],
        RB=[_pp("rb1", "RB", vorp=10.0)],
        WR=[_pp("wr1", "WR", vorp=9.0)],
    )
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}  # plan's QBs already done
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 10, counts, 9)
    assert pick.position not in ("DEF", "K")


def test_defk_forces_def_at_defk_round():
    avail = _avail(
        DEF=[_pp("def1", "DEF", vorp=5.0)],
        RB=[_pp("rb1", "RB", vorp=99.0)],
    )
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 14, counts, 5)
    assert pick.position == "DEF"


def test_defk_forces_k_at_defk_round_plus_one():
    avail = _avail(
        K=[_pp("k1", "K", vorp=5.0)],
        RB=[_pp("rb1", "RB", vorp=99.0)],
    )
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1, "DEF": 1}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 15, counts, 4)
    assert pick.position == "K"


def test_defk_force_prioritizes_def_over_k():
    # Both DEF and K unheld, round past both deadlines -> DEF forced first
    # (K's turn comes the following round).
    avail = _avail(
        DEF=[_pp("def1", "DEF", vorp=5.0)],
        K=[_pp("k1", "K", vorp=5.0)],
    )
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 16, counts, 3)
    assert pick.position == "DEF"


def test_caps_respected_no_fifth_qb():
    # 4 QBs held (cap) -> never a 5th, even with a huge-vorp QB on the board,
    # even past all deadlines (plan len=3, already exceeded).
    avail = _avail(
        QB=[_pp("qb5", "QB", vorp=500.0)],
        RB=[_pp("rb1", "RB", vorp=10.0)],
    )
    counts = {"QB": 4, "RB": 2, "WR": 3, "TE": 1}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 10, counts, 9)
    assert pick.position != "QB"
    assert pick.ref == "rb1"


def test_excludes_qb_beyond_plan_even_under_cap():
    # counts["QB"] == len(qb_by_round) (plan fully satisfied) but still under
    # cap (4) -> rule 4 must still exclude QB voluntarily.
    avail = _avail(
        QB=[_pp("qb4", "QB", vorp=500.0)],
        RB=[_pp("rb1", "RB", vorp=10.0)],
    )
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 10, counts, 9)
    assert pick.position != "QB"


def test_tier_break_prefers_last_in_tier():
    # Three RB candidates, equal vorp. A and B share tier 1 (neither is last
    # in tier); C is alone in tier 2 (is last in tier). With a positive
    # tier_break_bonus, C should win despite equal vorp.
    avail = _avail(
        RB=[
            _pp("rbA", "RB", vorp=10.0, tier=1),
            _pp("rbB", "RB", vorp=10.0, tier=1),
            _pp("rbC", "RB", vorp=10.0, tier=2),
        ],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}  # avoid QB deadline force
    params = StrategyParams(tier_break_bonus=0.5)
    fn = make_strategy_fn(params)
    pick = fn(avail, 6, counts, 12)
    assert pick.ref == "rbC"


def test_tier_break_bonus_zero_ignores_tier():
    # Same setup, but default tier_break_bonus=0.0 -> pure vorp tie, broken
    # by ADP/name, not tier-closing.
    avail = _avail(
        RB=[
            _pp("rbA", "RB", vorp=10.0, tier=1, adp=50.0),
            _pp("rbC", "RB", vorp=10.0, tier=2, adp=20.0),
        ],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 6, counts, 12)
    assert pick.ref == "rbC"  # lower adp (20 < 50) wins the tie, not tier


def test_deterministic_tiebreak_lower_adp_wins():
    avail = _avail(
        WR=[
            _pp("wrHigh", "WR", vorp=10.0, tier=1, adp=80.0),
            _pp("wrLow", "WR", vorp=10.0, tier=2, adp=50.0),
        ],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 6, counts, 12)
    assert pick.ref == "wrLow"


def test_deterministic_tiebreak_none_adp_last_then_name():
    # Equal vorp/tier-break score, both adp None -> tie-break falls to name
    # (alphabetically-first wins), giving a total order under equal score.
    avail = _avail(
        WR=[
            _pp("zzz", "WR", vorp=10.0, tier=1, adp=None),
            _pp("aaa", "WR", vorp=10.0, tier=1, adp=None),
        ],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 6, counts, 12)
    assert pick.ref == "aaa"


def test_deterministic_tiebreak_real_adp_beats_none():
    avail = _avail(
        WR=[
            _pp("wrReal", "WR", vorp=10.0, tier=1, adp=120.0),
            _pp("wrNone", "WR", vorp=10.0, tier=1, adp=None),
        ],
    )
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 6, counts, 12)
    assert pick.ref == "wrReal"


def test_returns_feasible_at_endgame():
    # required_picks(counts) == picks_left_after -> restrict to unmet
    # starter positions. QB is at its starter cap (2/2) and not flex
    # eligible, so it must be excluded from the restricted set even with a
    # monster vorp; RB/WR/TE (flex not yet covered) and K/DEF (unfilled) are
    # the only legal candidates.
    counts = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}  # required_picks == 3
    avail = _avail(
        QB=[_pp("qbHuge", "QB", vorp=999.0)],
        DEF=[_pp("def1", "DEF", vorp=5.0)],
        K=[_pp("k1", "K", vorp=50.0)],
    )
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 12, counts, 3)  # picks_left_after == required_picks == 3
    assert pick.position != "QB"
    assert pick.position == "K"  # highest vorp among the unmet set (K=50 > DEF=5)


def test_qb_deadline_force_falls_through_when_infeasible():
    # Plan wants QB #3 by round 9; counts already has 2 QBs (== starter req,
    # not flex-eligible) so a 3rd QB would NOT reduce required_picks. With
    # zero slack (required_picks(counts)=3 > picks_left_after=2, an
    # already-tight endgame), forcing that useless 3rd QB would violate
    # feasibility -> the force must fall through to the next legal rule
    # rather than return an infeasible pick that would make the draft engine
    # raise.
    counts = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    avail = _avail(
        QB=[_pp("qb3", "QB", vorp=999.0)],
        RB=[_pp("rb1", "RB", vorp=5.0)],
    )
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 9, counts, 2)  # round 9 triggers n=3's deadline
    assert pick.position != "QB"
    assert pick.position == "RB"


def test_defk_force_falls_through_when_infeasible():
    # DEF force triggers (round >= defk_round, no DEF held) but DEF has no
    # available candidates -- must fall through, not crash.
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}
    avail = _avail(RB=[_pp("rb1", "RB", vorp=5.0)])  # no DEF/K on the board
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 14, counts, 5)
    assert pick.position == "RB"


def test_raises_when_truly_nothing_feasible():
    # required_picks == 2 (K and DEF both still needed; RB/WR/TE/flex already
    # covered), but picks_left_after == 0 (this is the last pick) -- taking
    # either K or DEF alone still leaves one starter slot short, so every
    # position is infeasible despite candidates being on the board.
    counts = {"QB": 2, "RB": 3, "WR": 3, "TE": 1}
    avail = _avail(
        RB=[_pp("rb1", "RB", vorp=1.0)],
        K=[_pp("k1", "K", vorp=1.0)],
        DEF=[_pp("def1", "DEF", vorp=1.0)],
    )
    fn = make_strategy_fn(StrategyParams())
    import pytest

    with pytest.raises(ValueError):
        fn(avail, 19, counts, 0)


def test_cand_window_limits_depth():
    # Only the top CAND_WINDOW candidates per position are ever considered --
    # a monster-vorp player ranked below the window must not be picked.
    from ffi.sim.opponent import CAND_WINDOW

    deep_bench = [
        _pp(f"wr{i}", "WR", vorp=1.0, adp=float(i)) for i in range(CAND_WINDOW)
    ]
    deep_bench.append(_pp("wrBuried", "WR", vorp=1000.0, adp=float(CAND_WINDOW + 50)))
    avail = _avail(WR=deep_bench)
    counts = {"QB": 3, "RB": 0, "WR": 0, "TE": 0}
    fn = make_strategy_fn(StrategyParams())
    pick = fn(avail, 6, counts, 12)
    assert pick.ref != "wrBuried"


# --------------------------------------------------------------------------
# Integration: full run_draft with the real strategy fn
# --------------------------------------------------------------------------


def _integration_pool():
    """~350 synthetic PoolPlayers spanning a realistic position mix -- local
    copy of test_draft_engine._toy_pool's shape (kept independent rather than
    cross-importing a sibling test module)."""
    import numpy as np

    rng = np.random.default_rng(0)
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
        n_real_adp = int(n * 0.85)
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
    return players


def _integration_priors():
    from ffi.sim.draft import ROUNDS, TEAMS
    from ffi.sim.priors import SlotPriors

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


def test_qb_force_falls_through_when_cap_would_be_exceeded():
    # Plan wants QB #3 by round 9, but QB cap is 2. counts already has 2 QBs
    # (at cap). The QB force for n=3 should NOT force a 3rd QB since it would
    # exceed the cap -- instead it must fall through to rule 4 and pick another
    # position. This is the disclosed cap-respecting deviation from the brief's
    # literal decision order.
    counts = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    avail = _avail(
        QB=[_pp("qb3", "QB", vorp=999.0)],
        RB=[_pp("rb1", "RB", vorp=5.0)],
    )
    params = StrategyParams(
        qb_by_round=(1, 2, 3),
        caps=(("QB", 2), ("RB", 9), ("WR", 9), ("TE", 3), ("K", 2), ("DEF", 2)),
    )
    fn = make_strategy_fn(params)
    pick = fn(
        avail, 9, counts, 2
    )  # round 9 triggers n=3's deadline, but cap=2 prevents it
    assert pick.position != "QB", "QB force must fall through when it would exceed cap"
    assert pick.position == "RB"


def test_defk_force_falls_through_when_cap_is_zero():
    # DEF capped at 0 (roster can never legally hold DEF). At defk_round with
    # no DEF held, the DEF force condition checks `counts["DEF"] < cap`, which
    # is `0 < 0` (False), so the force falls through to rule 4 instead of
    # forcing DEF. This params combo (DEF cap=0) is only coherent for isolated
    # unit testing to pin the fall-through behavior; a real draft cannot satisfy
    # the roster constraint with DEF uncapped in starter slots.
    counts = {"QB": 3, "RB": 2, "WR": 3, "TE": 1}
    avail = _avail(
        RB=[_pp("rb1", "RB", vorp=5.0)],
        DEF=[_pp("def1", "DEF", vorp=200.0)],  # huge VORP but will not be forced
    )
    params = StrategyParams(
        caps=(("QB", 4), ("RB", 9), ("WR", 9), ("TE", 3), ("K", 2), ("DEF", 0))
    )
    fn = make_strategy_fn(params)
    pick = fn(
        avail, 14, counts, 5
    )  # round 14 is defk_round; DEF force must fall through
    assert pick.position != "DEF", "DEF force must fall through when cap is 0"
    assert pick.position == "RB"


def test_full_draft_our_roster_has_at_least_plan_qbs():
    from ffi.sim.draft import ROUNDS, run_draft

    pool = _integration_pool()
    priors = _integration_priors()
    params = StrategyParams()
    fn = make_strategy_fn(params)
    res = run_draft(pool, priors, fn, seed=123, our_position=6)
    our_roster = res.rosters[6]
    assert len(our_roster) == ROUNDS
    qb_count = sum(1 for p in our_roster if p.position == "QB")
    assert (
        qb_count >= 3
    ), f"expected >= 3 QBs (plan {params.qb_by_round}), got {qb_count}"
