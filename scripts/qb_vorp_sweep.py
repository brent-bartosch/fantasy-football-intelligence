#!/usr/bin/env python3
"""THROWAWAY analysis (brainstorm de-risking, not production): does lowering the
QB replacement rank (qb_hoard_24->12->0) improve our seat's ACTUAL-points finish?

Only QB VORP changes across the three hoard scenarios (compute_replacement_ranks:
qb_extra_rostered feeds QB demand alone), so we recompute QB VORP in-memory from
each backtest season's projections, re-run our seat against the same ADP-driven
opponents, and grade on real nflverse points. Opponents are unaffected (they
draft on ADP, not VORP), so any delta is purely our seat's response to QB VORP.

Sanity gate: recomputing at rank 36 must reproduce the stored qb_hoard_12 VORP.
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

from dataclasses import replace

from ffi.db import connect
from ffi.sim.backtest import BACKTEST_SEASONS, load_backtest_pool, load_points_lookup
from ffi.sim.draft import run_draft, snake_position
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import evaluate_league
from ffi.sim.strategy import StrategyParams, make_strategy_fn

N_SEEDS = 30
# rank = 24 starters + qb_extra_rostered. hoard_0/12/24 -> ranks 24/36/48.
SCENARIOS = {"hoard_0": 24, "hoard_12": 36, "hoard_24": 48}
# Deadlines OFF so QB VORP alone drives QB timing (feasibility still backstops
# the 2-QB requirement late) -- this maximally exposes the scenario effect.
STRAT = StrategyParams(qb_by_round=(19, 19, 19), qb_not_before=(1, 1, 1))


def qb_vorp_at_rank(pool, rank):
    """Return {ref: new_vorp} for QBs with replacement at `rank`; others unchanged."""
    qb_pts = sorted((p.proj_points for p in pool if p.position == "QB"), reverse=True)
    baseline = qb_pts[rank - 1]
    return {p.ref: p.proj_points - baseline for p in pool if p.position == "QB"}


def repriced_pool(pool, rank):
    new_vorp = qb_vorp_at_rank(pool, rank)
    return [replace(p, vorp=new_vorp[p.ref]) if p.position == "QB" else p for p in pool]


def main():
    conn = connect()
    priors = build_slot_priors(conn)

    # Sanity gate: rank-36 recompute vs stored hoard_12 VORP.
    p0 = load_backtest_pool(conn, BACKTEST_SEASONS[0])
    recomputed = qb_vorp_at_rank(p0, 36)
    stored = {p.ref: p.vorp for p in p0 if p.position == "QB"}
    max_diff = max(abs(recomputed[r] - stored[r]) for r in stored)
    print(f"SANITY: max |recomputed - stored| QB VORP at rank 36 = {max_diff:.3f}")
    if max_diff > 0.5:
        print("  !! recompute does not reproduce stored VORP -- results suspect")
    print()

    print(
        f"{'scenario':>9} {'season':>6} {'actual all-play%':>16} {'QBs':>5} {'QB1 rd':>7}"
    )
    agg = {s: [] for s in SCENARIOS}
    for name, rank in SCENARIOS.items():
        for season in BACKTEST_SEASONS:
            pool = repriced_pool(load_backtest_pool(conn, season), rank)
            lookup = load_points_lookup(conn, season)
            pick_fn = make_strategy_fn(STRAT)
            pcts, qb_counts, qb1_rounds = [], [], []
            for i in range(N_SEEDS):
                seed = 900_000 + season * 100 + i
                res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
                pcts.append(
                    evaluate_league(
                        res.rosters, cv_by_pos={}, seed=seed, points_lookup=lookup
                    )[res.our_position]
                )
                qbs = [
                    p
                    for p in res.picks
                    if p["position_slot"] == res.our_position and p["pos"] == "QB"
                ]
                qb_counts.append(len(qbs))
                qb1_rounds.append(min(snake_position(p["overall"])[0] for p in qbs))
            m = statistics.mean(pcts)
            agg[name].extend(pcts)
            print(
                f"{name:>9} {season:>6} {m:>15.1%} {statistics.mean(qb_counts):>5.1f} "
                f"{statistics.mean(qb1_rounds):>7.1f}"
            )

    print(f"\n{'scenario':>9} {'pooled actual all-play%':>24} {'2*SE':>8}")
    for name in SCENARIOS:
        vals = agg[name]
        m = statistics.mean(vals)
        se = statistics.stdev(vals) / (len(vals) ** 0.5)
        print(f"{name:>9} {m:>23.1%} {2*se:>7.1%}")


if __name__ == "__main__":
    main()
