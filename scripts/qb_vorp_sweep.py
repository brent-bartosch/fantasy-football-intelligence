#!/usr/bin/env python3
"""Guarded QB-rank search: which QB replacement rank (24/27/30/33/36) gives our
seat the best ACTUAL-points finish under the tuned strategy, without starving
depth or injury-robustness?

Only QB VORP changes across ranks (the replacement baseline shifts; proj_points
and -- per the rank-invariance finding -- tiers do not), so we recompute QB VORP
in-memory from each backtest season's projections, re-run our seat (tuned
strategy, `qb_by_round=(2,5,9)`) against the same ADP-driven opponents, and grade
on real nflverse points. Opponents are unaffected (they draft on ADP, not VORP),
so any delta is purely our seat's response to QB VORP. Each (rank, season) pool
is checked with `assert_tier_invariance` (R4 residual guard) before grading, and
each draft is scored on depth/injury guardrails alongside the headline all-play%:
avg QB count, whether we rostered a real QB3, injury-robustness (all-play% after
losing our best QB), and top-3 finish rate. Interpretation happens in Task 2.

Sanity gate: recomputing at rank 36 must reproduce the stored qb_hoard_12 VORP.
"""
import argparse
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
from ffi.sim.draft import run_draft
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import evaluate_league
from ffi.sim.strategy import StrategyParams, make_strategy_fn
from ffi.valuation.tiers import gmm_tiers

N_SEEDS = 50
# rank = 24 starters + qb_extra_rostered. Searched at 3-pick increments.
RANKS = [24, 27, 30, 33, 36]
# Tuned strategy (Phase 4 calibration): QB #1/2/3 targeted by end of rounds
# 2/5/9, capped at tier 1/2/99 (untiered) respectively.
STRAT = StrategyParams(qb_by_round=(2, 5, 9), qb_tier_targets=(1, 2, 99))


def qb_vorp_at_rank(pool, rank):
    """Return {ref: new_vorp} for QBs with replacement at `rank`; others unchanged."""
    qb_pts = sorted((p.proj_points for p in pool if p.position == "QB"), reverse=True)
    baseline = qb_pts[rank - 1]
    return {p.ref: p.proj_points - baseline for p in pool if p.position == "QB"}


def repriced_pool(pool, rank):
    """Pool with QB vorp recomputed at `rank`; tiers KEPT as-is. Tiers cluster
    on projected points, which `rank` never changes (it only shifts the QB
    baseline, a constant on vorp) -- so the stored tiers ARE this rank's tiers.
    No per-rank re-tiering or materialization needed."""
    new_vorp = qb_vorp_at_rank(pool, rank)
    return [replace(p, vorp=new_vorp[p.ref]) if p.position == "QB" else p for p in pool]


def _partition(labels):
    m, out = {}, []
    for x in labels:
        if x not in m:
            m[x] = len(m)
        out.append(m[x])
    return out


def assert_tier_invariance(pool):
    """R4 residual guard: regmm QB tiers on this pool's vorp and confirm it
    reproduces the stored tiers. A failure means the gmm implementation
    changed -- NOT that a rank collapsed tiers (which is impossible)."""
    qbs = [p for p in pool if p.position == "QB"]
    regmm = gmm_tiers([p.vorp for p in qbs])
    assert _partition(regmm) == _partition(
        [p.tier for p in qbs]
    ), "tier-invariance broken (gmm impl changed?)"


def injury_robustness(rosters, our_pos, points_lookup, seed):
    """Our actual-points all-play% AFTER losing our best (highest-vorp) QB --
    a roster with a real QB3 barely drops; a thin one craters (one QB slot
    scores 0). Directly measures the QB3-protection guardrail."""
    roster = list(rosters[our_pos])
    qbs = [p for p in roster if p.position == "QB"]
    qb1 = max(qbs, key=lambda p: p.vorp)
    injured = dict(rosters)
    injured[our_pos] = [p for p in roster if p.ref != qb1.ref]
    return evaluate_league(
        injured, cv_by_pos={}, seed=seed, points_lookup=points_lookup
    )[our_pos]


def _selftest():
    # repriced_pool: QB vorp changes with rank, but tiers are KEPT (rank-invariant)
    # NOTE: brief's synthetic pool used range(5), but qb_vorp_at_rank indexes
    # qb_pts[rank - 1] with no bounds check, so a 5-QB pool + rank=24 raises
    # IndexError (found in RED run). Widened to 30 QBs -- enough for rank=24
    # to be a valid index -- while keeping the same assertions/intent.
    qbs = [
        PoolPlayer(
            ref=f"q{i}",
            name=f"QB{i}",
            position="QB",
            proj_points=400 - 3 * i,
            vorp=300 - 4 * i,
            tier=(1 if i < 2 else 2),
            adp=float(i + 1),
            gsis_id=f"q{i}",
        )
        for i in range(30)
    ]
    rp = repriced_pool(qbs, 24)
    assert [p.vorp for p in rp] != [p.vorp for p in qbs], "rank 24 must change QB vorp"
    assert [p.tier for p in rp] == [
        p.tier for p in qbs
    ], "tiers must be KEPT (rank-invariant)"
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--selftest", action="store_true", help="run helper self-test and exit"
    )
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return

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
        f"{'rank':>5} {'season':>6} {'all-play%':>10} {'2SE':>6} {'QBs':>5} "
        f"{'QB3%':>6} {'injury%':>8} {'top3%':>7}"
    )
    metrics = ("pct", "qb_count", "has_qb3", "injury", "top3")
    pooled = {rank: {k: [] for k in metrics} for rank in RANKS}
    for rank in RANKS:
        for season in BACKTEST_SEASONS:
            pool = repriced_pool(load_backtest_pool(conn, season), rank)
            assert_tier_invariance(pool)  # R4 residual guard (tiers are rank-invariant)
            lookup = load_points_lookup(conn, season)
            pick_fn = make_strategy_fn(STRAT)
            cell = {k: [] for k in metrics}
            for i in range(N_SEEDS):
                seed = 900_000 + season * 100 + i
                res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
                standings = evaluate_league(
                    res.rosters, cv_by_pos={}, seed=seed, points_lookup=lookup
                )
                our_pct = standings[res.our_position]
                place = sum(1 for v in standings.values() if v > our_pct) + 1
                qbs = [
                    p
                    for p in res.picks
                    if p["position_slot"] == res.our_position and p["pos"] == "QB"
                ]
                cell["pct"].append(our_pct)
                cell["qb_count"].append(len(qbs))
                cell["has_qb3"].append(len(qbs) >= 3)
                cell["injury"].append(
                    injury_robustness(res.rosters, res.our_position, lookup, seed)
                )
                cell["top3"].append(place <= 3)
            for k in metrics:
                pooled[rank][k].extend(cell[k])
            se = statistics.stdev(cell["pct"]) / (len(cell["pct"]) ** 0.5)
            print(
                f"{rank:>5} {season:>6} {statistics.mean(cell['pct']):>9.1%} "
                f"{2 * se:>5.1%} {statistics.mean(cell['qb_count']):>5.1f} "
                f"{statistics.mean(cell['has_qb3']):>5.1%} "
                f"{statistics.mean(cell['injury']):>7.1%} "
                f"{statistics.mean(cell['top3']):>6.1%}"
            )

    print(
        f"\n{'rank':>5} {'pooled all-play%':>17} {'2SE':>6} {'QBs':>5} "
        f"{'QB3%':>6} {'injury%':>8} {'top3%':>7}"
    )
    for rank in RANKS:
        vals = pooled[rank]
        se = statistics.stdev(vals["pct"]) / (len(vals["pct"]) ** 0.5)
        print(
            f"{rank:>5} {statistics.mean(vals['pct']):>16.1%} {2 * se:>5.1%} "
            f"{statistics.mean(vals['qb_count']):>5.1f} "
            f"{statistics.mean(vals['has_qb3']):>5.1%} "
            f"{statistics.mean(vals['injury']):>7.1%} "
            f"{statistics.mean(vals['top3']):>6.1%}"
        )


if __name__ == "__main__":
    main()
