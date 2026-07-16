#!/usr/bin/env python3
"""Positional-depth test: VORP over-values bench depth at single-start positions
(you start 1 TE, 2 QB). Does capping TE/QB depth and letting those picks flow to
RB/WR (which actually start via 2RB+3WR+FLEX+injuries) improve playoff odds?

Holds the QB3-delay finding (qb_not_before=(1,1,13)); varies the TE and QB caps;
grades on actual points via season all-play AND round-robin H2H playoff-make, and
shows the resulting roster shape so the redirection is visible.
"""
import os
import statistics
from collections import Counter
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
# (QB cap, TE cap). Current default is QB4/TE3.
CAP_CONDITIONS = [(4, 3), (3, 2), (3, 1), (2, 1)]


def caps(qb, te):
    return (("QB", qb), ("RB", 9), ("WR", 9), ("TE", te), ("K", 1), ("DEF", 1))


def round_robin(n):
    teams, rounds = list(range(n)), []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


ROUNDS = round_robin(12)


def h2h_rank(rosters, lookup, our):
    tk, flat, pos_idx = _build_index(rosters)
    pts = _lookup_weekly_points(flat, lookup)
    wt = {t: _lineup_total(pts, pos_idx[t])[0] for t in tk}
    wins = {t: 0 for t in tk}
    for w in range(REG_WEEKS):
        for a, b in ROUNDS[w % len(ROUNDS)]:
            ta, tb = tk[a], tk[b]
            wins[ta if wt[ta][w] >= wt[tb][w] else tb] += 1
    return sum(1 for t in tk if wins[t] > wins[our]) + 1


def main():
    conn = connect()
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}

    print(
        f"{'QB/TE cap':>10} {'all-play%':>10} {'playoff%':>9} {'roster (QB/RB/WR/TE)':>22}"
    )
    for qb, te in CAP_CONDITIONS:
        strat = StrategyParams(
            scenario="qb_hoard_12",
            qb_by_round=(6, 11, 16),
            qb_not_before=(1, 1, 13),
            qb_tier_targets=(),
            caps=caps(qb, te),
        )
        pick_fn = make_strategy_fn(strat)
        ap, playoff, comp = [], [], Counter()
        n = 0
        for season in BACKTEST_SEASONS:
            pool, lookup = pools[season], lookups[season]
            for i in range(N_SEEDS):
                seed = 400_000 + season * 100 + i
                res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
                ap.append(
                    evaluate_league(
                        res.rosters, cv_by_pos={}, seed=seed, points_lookup=lookup
                    )[res.our_position]
                )
                playoff.append(h2h_rank(res.rosters, lookup, res.our_position) <= 6)
                for p in res.rosters[res.our_position]:
                    comp[p.position] += 1
                n += 1
        shape = "/".join(f"{comp[p] / n:.1f}" for p in ("QB", "RB", "WR", "TE"))
        print(
            f"{str(qb) + '/' + str(te):>10} {statistics.mean(ap):>9.1%} "
            f"{statistics.mean(playoff):>8.0%} {shape:>22}"
        )


if __name__ == "__main__":
    main()
