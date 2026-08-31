import pytest

from ffi.sim.pool import PoolPlayer
from ffi.sim.season import evaluate_league

import waiver_ceiling_test as wct  # scripts/ is on sys.path via conftest


def _p(ref, position, points_per_week, gsis=None):
    return PoolPlayer(
        ref=ref,
        name=ref,
        position=position,
        proj_points=points_per_week * 14,
        vorp=0.0,
        tier=1,
        adp=None,
        gsis_id=gsis if gsis is not None else ref,
    )


def _full_roster(prefix, base):
    """2 QB + 2 RB + 3 WR + 1 TE + 1 K + 1 DEF starters, plus 1 flex bench."""
    spec = [("QB", 2), ("RB", 3), ("WR", 3), ("TE", 1), ("K", 1), ("DEF", 1)]
    roster, i = [], 0
    for pos, n in spec:
        for j in range(n):
            roster.append(_p(f"{prefix}-{pos}{j}", pos, base + i))
            i += 1
    return roster


def _lookup(rosters, weeks=14):
    out = {}
    for roster in rosters.values():
        for p in roster:
            for w in range(1, weeks + 1):
                out[(p.gsis_id, w)] = p.proj_points / 14
    return out


def test_lineup_total_matches_the_library_evaluator():
    """The harness reimplements the lineup rule scalar-side for speed. If it
    ever drifts from ffi.sim.season, every number this script reports is
    wrong in a way nobody would notice."""
    rosters = {1: _full_roster("A", 10.0), 2: _full_roster("B", 5.0)}
    lookup = _lookup(rosters)
    library = evaluate_league(rosters, cv_by_pos={}, seed=1, points_lookup=lookup)
    harness = wct.all_play_pct(rosters, lookup)
    assert harness == pytest.approx(library, abs=1e-9)


def test_perfect_foresight_never_lowers_the_roster():
    roster = _full_roster("A", 5.0)
    fa = [_p("FA-RB", "RB", 99.0), _p("FA-WR", "WR", 98.0)]
    lookup = _lookup({1: roster + fa})
    before = sum(wct.lineup_total(roster, w, lookup) for w in range(1, 15))
    improved, adds = wct.perfect_foresight_roster(roster, fa, lookup)
    after = sum(wct.lineup_total(improved, w, lookup) for w in range(1, 15))
    assert after >= before
    assert adds > 0


def test_perfect_foresight_respects_the_weekly_add_limit():
    roster = _full_roster("A", 1.0)
    fa = [_p(f"FA{i}", "WR", 90.0 + i) for i in range(30)]
    lookup = _lookup({1: roster + fa})
    _, adds = wct.perfect_foresight_roster(
        roster, fa, lookup, weekly_limit=2, season_limit=99
    )
    assert adds <= 2 * 14


def test_perfect_foresight_respects_the_season_add_limit():
    roster = _full_roster("A", 1.0)
    fa = [_p(f"FA{i}", "WR", 90.0 + i) for i in range(30)]
    lookup = _lookup({1: roster + fa})
    _, adds = wct.perfect_foresight_roster(
        roster, fa, lookup, weekly_limit=5, season_limit=3
    )
    assert adds == 3


def test_a_position_is_never_dropped_below_its_starter_requirement():
    roster = _full_roster("A", 1.0)
    fa = [_p(f"FA{i}", "WR", 90.0 + i) for i in range(30)]
    lookup = _lookup({1: roster + fa})
    improved, _ = wct.perfect_foresight_roster(roster, fa, lookup)
    counts = {}
    for p in improved:
        counts[p.position] = counts.get(p.position, 0) + 1
    from ffi.sim.opponent import STARTERS

    for pos, need in STARTERS.items():
        assert counts.get(pos, 0) >= need, f"{pos} dropped below {need}"


def test_no_free_agents_means_no_adds():
    roster = _full_roster("A", 5.0)
    lookup = _lookup({1: roster})
    improved, adds = wct.perfect_foresight_roster(roster, [], lookup)
    assert adds == 0
    assert improved == roster
