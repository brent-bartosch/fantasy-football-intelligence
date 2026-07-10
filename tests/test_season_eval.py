"""Tests for the season evaluator (Phase 3 / Task 9)."""
import numpy as np
import pytest

from ffi.sim.pool import PoolPlayer
from ffi.sim.season import (
    BYE_WINDOW,
    REG_WEEKS,
    _lineup_total,
    _lookup_weekly_points,
    _mc_weekly_points,
    evaluate_league,
    fit_weekly_points_cv,
)

# --------------------------------------------------------------------------
# Toy fixtures
# --------------------------------------------------------------------------


def _player(ref, position, proj_points=100.0, gsis_id=None):
    return PoolPlayer(
        ref=ref,
        name=ref,
        position=position,
        proj_points=proj_points,
        vorp=0.0,
        tier=1,
        adp=None,
        gsis_id=gsis_id,
    )


def _legal_roster(
    team_id: int, proj_points: float = 100.0, gsis_prefix: str | None = None
):
    """Minimal-but-legal roster: 2 QB, 3 RB (1 bench->flex), 4 WR (1
    bench->flex), 2 TE (1 bench->flex), 1 K, 1 DEF."""
    specs = [("QB", 2), ("RB", 3), ("WR", 4), ("TE", 2), ("K", 1), ("DEF", 1)]
    roster = []
    for pos, n in specs:
        for i in range(n):
            gid = f"{gsis_prefix}_{pos}{i}" if gsis_prefix else None
            roster.append(_player(f"t{team_id}_{pos}{i}", pos, proj_points, gid))
    return roster


def _toy_rosters(proj_points: float = 100.0):
    return {
        t: _legal_roster(t, proj_points, gsis_prefix=f"team{t}") for t in range(1, 13)
    }


_CV_BY_POS = {"QB": 0.6, "RB": 0.8, "WR": 0.8, "TE": 0.85, "K": 0.55, "DEF": 0.7}


# --------------------------------------------------------------------------
# _lineup_total: hand-computed optimality
# --------------------------------------------------------------------------


def test_lineup_is_optimal_on_toy_roster():
    # QB: [10, 8] -> both starters, no leftover (need=2, have=2)
    # RB: [20, 15, 5] -> top2 = 35, leftover [5]
    # WR: [30, 25, 10, 2] -> top3 = 65, leftover [2]
    # TE: [12, 3] -> top1 = 12, leftover [3]
    # K: [9] -> 9
    # DEF: [7] -> 7
    # FLEX: best of leftovers {5, 2, 3} = 5 (the leftover RB)
    pos_idx = {
        "QB": [0, 1],
        "RB": [2, 3, 4],
        "WR": [5, 6, 7, 8],
        "TE": [9, 10],
        "K": [11],
        "DEF": [12],
    }
    points = np.array([10, 8, 20, 15, 5, 30, 25, 10, 2, 12, 3, 9, 7], dtype=float)
    total = _lineup_total(points, pos_idx)
    expected = (10 + 8) + (20 + 15) + (30 + 25 + 10) + 12 + 9 + 7 + 5
    assert float(total) == pytest.approx(expected)


def test_lineup_total_is_vectorized_over_leading_dims():
    # Same roster shape, but stacked as (2 "weeks", P) -- output should be
    # the per-week optimal total, independently.
    pos_idx = {"RB": [0, 1, 2]}
    points = np.array([[10.0, 20.0, 5.0], [1.0, 2.0, 30.0]])
    total = _lineup_total(points, pos_idx)
    # RB needs=2: week0 top2 = 20+10=30, leftover [5] -> flex=5 -> 35
    # week1 top2 = 30+2=32, leftover [1] -> flex=1 -> 33
    assert total.tolist() == pytest.approx([35.0, 33.0])


# --------------------------------------------------------------------------
# points_lookup mode: deterministic + exact
# --------------------------------------------------------------------------


def test_points_lookup_mode_is_deterministic_and_exact():
    # 12 teams, each with a single K player worth exactly `i` points every
    # week (i = 1..12, draft position == team rank). Since there's no
    # RB/WR/TE at all, there's no possible FLEX leftover, so each team's
    # weekly total is exactly its K's points -- fully hand-computable:
    # team i beats every team j < i (worse) each week, every week -> wins
    # (i-1) of 11 opponents per week, constant across all 14 weeks.
    rosters = {
        i: [_player(f"k{i}", "K", proj_points=0.0, gsis_id=f"g{i}")]
        for i in range(1, 13)
    }
    lookup = {
        (f"g{i}", w): float(i) for i in range(1, 13) for w in range(1, REG_WEEKS + 1)
    }

    result = evaluate_league(rosters, cv_by_pos={}, seed=0, points_lookup=lookup)

    assert set(result.keys()) == set(range(1, 13))
    for i in range(1, 13):
        assert result[i] == pytest.approx((i - 1) / 11)


def test_points_lookup_mode_ignores_n_seasons():
    rosters = {
        i: [_player(f"k{i}", "K", proj_points=0.0, gsis_id=f"g{i}")]
        for i in range(1, 13)
    }
    lookup = {
        (f"g{i}", w): float(i) for i in range(1, 13) for w in range(1, REG_WEEKS + 1)
    }
    r1 = evaluate_league(
        rosters, cv_by_pos={}, seed=0, n_seasons=5, points_lookup=lookup
    )
    r2 = evaluate_league(
        rosters, cv_by_pos={}, seed=0, n_seasons=999, points_lookup=lookup
    )
    assert r1 == r2


def test_missing_player_week_scores_zero_in_lookup_mode():
    players = [
        _player("p1", "QB", gsis_id="gA"),
        _player("p2", "QB", gsis_id="gB"),
        _player("p3", "QB", gsis_id=None),
    ]
    lookup = {("gA", 1): 12.5}  # gA week1 present; everything else absent
    points = _lookup_weekly_points(players, lookup)
    assert points.shape == (1, REG_WEEKS, 3)
    assert points[0, 0, 0] == pytest.approx(12.5)  # gA, week1 -> present
    assert points[0, 1, 0] == 0.0  # gA, week2 -> missing -> 0
    assert np.all(points[0, :, 1] == 0.0)  # gB never in lookup -> all 0
    assert np.all(points[0, :, 2] == 0.0)  # gsis_id=None -> always 0


def test_none_gsis_id_scores_zero_even_if_dict_has_a_none_key():
    # Contract: gsis_id=None is a hardcoded 0.0, not merely ".get" fallback --
    # even if the caller's dict happens to contain a (None, week) entry, a
    # None-gsis player must still score 0.
    players = [_player("p1", "DEF", gsis_id=None)]
    lookup = {(None, 1): 999.0}
    points = _lookup_weekly_points(players, lookup)
    assert np.all(points == 0.0)


# --------------------------------------------------------------------------
# Monte Carlo mode: determinism, bye zeroing, dominance
# --------------------------------------------------------------------------


def test_mc_mode_deterministic_by_seed():
    rosters = _toy_rosters()
    r1 = evaluate_league(rosters, _CV_BY_POS, seed=42, n_seasons=5)
    r2 = evaluate_league(rosters, _CV_BY_POS, seed=42, n_seasons=5)
    assert r1 == r2


def test_mc_mode_different_seeds_diverge():
    rosters = _toy_rosters()
    r1 = evaluate_league(rosters, _CV_BY_POS, seed=1, n_seasons=5)
    r2 = evaluate_league(rosters, _CV_BY_POS, seed=2, n_seasons=5)
    assert r1 != r2


def test_strictly_dominant_roster_wins_more():
    rosters = _toy_rosters(proj_points=100.0)
    rosters[1] = _legal_roster(1, proj_points=200.0, gsis_prefix="team1")  # dominant
    result = evaluate_league(rosters, _CV_BY_POS, seed=7, n_seasons=20)
    assert result[1] > result[2]
    assert result[1] > max(v for k, v in result.items() if k != 1)


def test_bye_week_zeroes_exactly_one_week_in_window():
    players = [
        _player("p1", "QB"),
        _player("p2", "RB"),
        _player("p3", "WR"),
        _player("p4", "TE"),
        _player("p5", "K"),
        _player("p6", "DEF"),
    ]
    n_seasons = 50
    points = _mc_weekly_points(players, _CV_BY_POS, seed=123, n_seasons=n_seasons)
    assert points.shape == (n_seasons, REG_WEEKS, len(players))
    for s in range(n_seasons):
        for p in range(len(players)):
            zero_weeks = [w for w in range(REG_WEEKS) if points[s, w, p] == 0.0]
            assert len(zero_weeks) == 1, (s, p, zero_weeks)
            week_number = zero_weeks[0] + 1
            assert BYE_WINDOW[0] <= week_number <= BYE_WINDOW[1]


def test_mc_mode_missing_cv_position_fails_loud():
    rosters = {1: [_player("p1", "QB")]}
    with pytest.raises(ValueError, match="QB"):
        evaluate_league(rosters, cv_by_pos={}, seed=0, n_seasons=2)


# --------------------------------------------------------------------------
# CV fit: fail-loud on missing data (DB-backed)
# --------------------------------------------------------------------------


def test_fit_weekly_points_cv_fails_loud_on_missing_position(db):
    with pytest.raises(ValueError, match="QB"):
        fit_weekly_points_cv(db)
