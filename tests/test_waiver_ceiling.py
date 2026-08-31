import pytest

from ffi.sim.draft import DraftResult
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
    """ROSTER property, NOT an evaluation pattern. Summing `lineup_total` over
    the FINAL roster for all 14 weeks — as this test does — is exactly the
    retroactive-credit trap that `test_an_add_is_not_credited_retroactively`
    forbids for scoring a season. It is legitimate here only because the claim
    being made is about the roster ("the swaps left us with better players"),
    not about what we scored in any particular week. Do not copy this pattern
    into anything week-indexed.

    Note this is also not a monotonicity proof: the greedy takes a week-w gain
    for a permanent drop, so a season-sum decrease is possible in principle. It
    cannot happen on THIS fixture (the free agents dominate every roster player
    in every week), and empirically never happened in 900 measured drafts.
    """
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


def test_an_add_is_not_credited_retroactively():
    """The trap that made the first run report a NEGATIVE oracle in 2024: score
    the FINAL end-of-season roster over all 14 weeks and a week-14 pickup gets
    credited for weeks 1-13 while the player he replaced is erased from weeks
    he actually played. Our team must be scored each week with the roster we
    actually held that week."""
    roster = _full_roster("A", 5.0)
    late = _p("FA-LATE", "RB", 0.0)
    lookup = _lookup({1: roster})
    for w in range(1, 15):
        lookup[("FA-LATE", w)] = 0.0
    lookup[("FA-LATE", 14)] = 500.0  # one enormous week, and only that week

    held, adds = wct.perfect_foresight_weekly(roster, [late], lookup)
    assert adds == 1, "the week-14 spike is the only swap worth making"
    assert late not in held[1], "credited before he was ever added"
    assert late in held[14]
    # Week 1 must be indistinguishable from having done nothing.
    assert wct.lineup_total(held[1], 1, lookup) == pytest.approx(
        wct.lineup_total(roster, 1, lookup)
    )
    # Every pre-add week must be identical to doing nothing, and scoring the
    # FINAL roster in those weeks must be provably wrong (it is missing the
    # player who was dropped in week 14 but actually played weeks 1-13).
    for w in range(1, 14):
        assert wct.lineup_total(held[w], w, lookup) == pytest.approx(
            wct.lineup_total(roster, w, lookup)
        )
        assert wct.lineup_total(held[14], w, lookup) < wct.lineup_total(
            roster, w, lookup
        ), f"retroactive scoring corrupts week {w}"

    # Season-level. The opponent is deliberately placed BETWEEN the two totals
    # so the two evaluations of the same policy result cannot agree:
    #   our real weeks 1-13 total  = 110.0   (the drafted roster, untouched)
    #   our final-roster total     = 103.0   (the week-14 drop erased from them)
    #   opponent B (base 4.9)      = 108.9   -> strictly between the two
    # so week-accurate scoring wins all 14 weeks (13 on merit + the spike week)
    # while retroactive scoring LOSES weeks 1-13 and wins only week 14.
    # An identical twin at 5.0 would tie weeks 1-13 under both evaluations and
    # report 1/14 either way — a test that cannot see the bug it is named for.
    rosters = {1: held[wct.REG_WEEKS], 2: _full_roster("B", 4.9)}
    lookup.update(_lookup({2: rosters[2]}))

    weekly = wct.all_play_pct(rosters, lookup, weekly_rosters={1: held})
    assert weekly[1] == pytest.approx(1.0), "week-accurate: we win all 14 weeks"

    # The buggy path, spelled out: score the FINAL roster every week (which is
    # what `rosters` alone holds). 1/14 — and that 13-week gap is the entire
    # difference between the +29.07pp ceiling and the negative-oracle nonsense.
    retroactive = wct.all_play_pct(rosters, lookup)
    assert retroactive[1] == pytest.approx(1 / 14)


def test_run_season_scores_our_team_week_accurately(monkeypatch):
    """Binds the ONLY caller of the weekly path. Every other test here pins
    `perfect_foresight_weekly` and `all_play_pct` in isolation, so `run_season`
    could revert to `perfect_foresight_roster` + plain `all_play_pct` — the exact
    original bug — with the whole file still green. Same week-14-spike fixture,
    driven through `run_season` with the DB and the draft stubbed out."""
    ours = _full_roster("A", 5.0)
    theirs = _full_roster("B", 4.9)
    late = _p("FA-LATE", "RB", 0.0)
    lookup = _lookup({1: ours, 2: theirs})
    for w in range(1, 15):
        lookup[("FA-LATE", w)] = 0.0
    lookup[("FA-LATE", 14)] = 500.0
    # The opponent spikes in week 14 too, so DOING NOTHING loses that week. That
    # separates all three candidate evaluations: do-nothing 13/14, week-accurate
    # 14/14, retroactive 1/14. Without it, do-nothing and week-accurate tie.
    lookup[("B-QB0", 14)] = 200.0

    result = DraftResult(rosters={1: ours, 2: theirs}, our_position=1)
    monkeypatch.setattr(wct, "load_backtest_pool", lambda *a: ours + theirs + [late])
    monkeypatch.setattr(wct, "load_points_lookup", lambda *a: lookup)
    monkeypatch.setattr(wct, "make_strategy_fn", lambda *a: None)
    monkeypatch.setattr(wct, "cell_base_seed", lambda *a: 7)
    monkeypatch.setattr(wct, "run_draft", lambda *a, **k: result)

    (cell,) = wct.run_season(conn=None, priors=None, season=2025, n_drafts=1)
    assert cell["adds"] == 1, "only the week-14 spike is worth a swap"
    assert cell["base_pct"] == pytest.approx(13 / 14), "do-nothing loses week 14"
    assert cell["foresight_pct"] == pytest.approx(1.0), "week-accurate: 14/14"
    assert cell["foresight_pct"] != pytest.approx(1 / 14), "retroactive value"


def test_no_free_agents_means_no_adds():
    roster = _full_roster("A", 5.0)
    lookup = _lookup({1: roster})
    improved, adds = wct.perfect_foresight_roster(roster, [], lookup)
    assert adds == 0
    assert improved == roster
