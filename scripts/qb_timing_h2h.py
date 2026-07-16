#!/usr/bin/env python3
"""QB3-timing as a TREND on BOTH metrics: does delaying QB3 win on H2H
(playoff%) the way it wins on season all-play, or do they diverge? Varies the
QB3 delay (qb_not_before[2]) keeping QB1/QB2 early, grades each on actual points
via season all-play AND a 12-team round-robin H2H (wins / playoff-make / bye
holes). Not to pick a magic round -- to see the shape.
"""
import os
import statistics
from pathlib import Path

env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from ffi.db import connect
from ffi.sim.backtest import BACKTEST_SEASONS, load_backtest_pool, load_points_lookup
from ffi.sim.draft import run_draft
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import (
    REG_WEEKS,
    _build_index,
    _lineup_total,
    _lookup_weekly_points,
    evaluate_league,
)
from ffi.sim.strategy import StrategyParams, make_strategy_fn

N_SEEDS = 50
# QB3 not-before round (QB1/QB2 kept early at 1); by_round backstop stays loose.
QB3_DELAYS = [1, 6, 9, 13]


def round_robin(n):
    teams, rounds = list(range(n)), []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


ROUNDS = round_robin(12)


def weekly_totals(rosters, lookup):
    tk, flat, pos_idx = _build_index(rosters)
    pts = _lookup_weekly_points(flat, lookup)
    return tk, {t: _lineup_total(pts, pos_idx[t])[0] for t in tk}


def h2h_rank(tk, wt, our):
    wins = {t: 0 for t in tk}
    for w in range(REG_WEEKS):
        for a, b in ROUNDS[w % len(ROUNDS)]:
            ta, tb = tk[a], tk[b]
            winner = ta if wt[ta][w] >= wt[tb][w] else tb
            wins[winner] += 1
    return wins[our], sum(1 for t in tk if wins[t] > wins[our]) + 1


def qb_hole_weeks(roster, lookup):
    qbs = [p for p in roster if p.position == "QB" and p.gsis_id]
    return sum(
        1
        for w in range(1, REG_WEEKS + 1)
        if sum(1 for q in qbs if lookup.get((q.gsis_id, w), 0.0) > 0) < 2
    )


def main():
    conn = connect()
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}

    print(
        f"{'QB3 delay':>10} {'all-play%':>10} {'H2H wins':>9} {'playoff%':>9} {'bye-holes':>10}"
    )
    for d in QB3_DELAYS:
        strat = StrategyParams(
            scenario="qb_hoard_12",
            qb_by_round=(6, 11, 16),
            qb_not_before=(1, 1, d),
            qb_tier_targets=(),
        )
        pick_fn = make_strategy_fn(strat)
        ap, wins, playoff, holes = [], [], [], []
        for season in BACKTEST_SEASONS:
            pool, lookup = pools[season], lookups[season]
            for i in range(N_SEEDS):
                seed = 500_000 + season * 100 + i
                res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
                ap.append(
                    evaluate_league(
                        res.rosters, cv_by_pos={}, seed=seed, points_lookup=lookup
                    )[res.our_position]
                )
                tk, wt = weekly_totals(res.rosters, lookup)
                w, rank = h2h_rank(tk, wt, res.our_position)
                wins.append(w)
                playoff.append(rank <= 6)
                holes.append(qb_hole_weeks(res.rosters[res.our_position], lookup))
        print(
            f"{'~R'+str(d):>10} {statistics.mean(ap):>9.1%} {statistics.mean(wins):>9.2f} "
            f"{statistics.mean(playoff):>8.0%} {statistics.mean(holes):>10.2f}"
        )


if __name__ == "__main__":
    main()
