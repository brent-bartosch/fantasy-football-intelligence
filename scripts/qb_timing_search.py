#!/usr/bin/env python3
"""Light stress-test (no risk/ADR cycle): does WAITING on QB -- and delaying QB3
specifically -- improve our seat's ACTUAL-points finish? Varies `qb_not_before`
(the real delay lever; qb_by_round never binds because high QB VORP front-loads
QBs) at the fixed qb_hoard_12 baseline, grades on real nflverse points with the
same depth/injury guardrails, and reports WHEN each QB actually gets taken.

Byes are already reflected in the actual-points grade (a bye QB scores 0 and
`_lineup_total` starts the best-available QB that week); bye-AWARE drafting is a
separate follow-up.
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
from ffi.sim.draft import run_draft, snake_position
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import evaluate_league
from ffi.sim.strategy import StrategyParams, make_strategy_fn

N_SEEDS = 100
# qb_not_before[n] = QB #(n+1) not draftable before this round. by_round is a
# loose backstop (>= every not_before so no misconfig); tier_targets off to
# isolate the TIMING effect.
BY_ROUND = (6, 11, 16)
PLANS = {
    "front (1,1,1)": (1, 1, 1),
    "QB3@10 (1,1,10)": (1, 1, 10),
    "QB3@13 (1,1,13)": (1, 1, 13),
    "wait2 (2,5,10)": (2, 5, 10),
    "wait3 (3,6,10)": (3, 6, 10),
    "wait4 (4,8,13)": (4, 8, 13),
}


def injury_robustness(rosters, our_pos, points_lookup, seed):
    roster = list(rosters[our_pos])
    qbs = [p for p in roster if p.position == "QB"]
    qb1 = max(qbs, key=lambda p: p.vorp)
    injured = dict(rosters)
    injured[our_pos] = [p for p in roster if p.ref != qb1.ref]
    return evaluate_league(
        injured, cv_by_pos={}, seed=seed, points_lookup=points_lookup
    )[our_pos]


def qb_take_rounds(picks, our_pos):
    qbs = sorted(
        (p for p in picks if p["position_slot"] == our_pos and p["pos"] == "QB"),
        key=lambda p: p["overall"],
    )
    return [snake_position(p["overall"])[0] for p in qbs]


def main():
    conn = connect()
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}

    print(
        f"{'plan':>16} {'all-play%':>10} {'2SE':>6} {'QBs':>4} {'QB3%':>6} "
        f"{'injury%':>8} {'top3%':>6} {'QB1rd':>6} {'QB2rd':>6} {'QB3rd':>6}"
    )
    for label, not_before in PLANS.items():
        strat = StrategyParams(
            scenario="qb_hoard_12",
            qb_by_round=BY_ROUND,
            qb_not_before=not_before,
            qb_tier_targets=(),
        )
        pick_fn = make_strategy_fn(strat)
        pct, qbn, qb3, inj, top3, r1, r2, r3 = ([] for _ in range(8))
        for season in BACKTEST_SEASONS:
            pool, lookup = pools[season], lookups[season]
            for i in range(N_SEEDS):
                seed = 800_000 + season * 100 + i
                res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
                standings = evaluate_league(
                    res.rosters, cv_by_pos={}, seed=seed, points_lookup=lookup
                )
                our = standings[res.our_position]
                pct.append(our)
                top3.append((sum(1 for v in standings.values() if v > our) + 1) <= 3)
                rounds = qb_take_rounds(res.picks, res.our_position)
                qbn.append(len(rounds))
                qb3.append(len(rounds) >= 3)
                inj.append(
                    injury_robustness(res.rosters, res.our_position, lookup, seed)
                )
                if len(rounds) >= 1:
                    r1.append(rounds[0])
                if len(rounds) >= 2:
                    r2.append(rounds[1])
                if len(rounds) >= 3:
                    r3.append(rounds[2])
        se2 = 2 * statistics.stdev(pct) / (len(pct) ** 0.5)
        print(
            f"{label:>16} {statistics.mean(pct):>9.1%} {se2:>5.1%} "
            f"{statistics.mean(qbn):>4.1f} {statistics.mean(qb3):>5.0%} "
            f"{statistics.mean(inj):>7.1%} {statistics.mean(top3):>5.0%} "
            f"{statistics.mean(r1):>6.1f} {statistics.mean(r2):>6.1f} "
            f"{(statistics.mean(r3) if r3 else float('nan')):>6.1f}"
        )


if __name__ == "__main__":
    main()
