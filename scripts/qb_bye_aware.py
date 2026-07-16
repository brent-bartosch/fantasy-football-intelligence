#!/usr/bin/env python3
"""Light stress-test: does BYE-AWARE QB drafting (never let two of our QBs share
a bye week -- "don't own both the Falcons and Rams QBs when both bye in wk5")
beat bye-blind drafting, on top of the QB3-delay finding?

Both conditions run the delayed strategy (qb_not_before=(1,1,13), qb_hoard_12)
and grade on actual nflverse points. The bye-aware condition wraps the pick fn:
when the strategy would take a QB whose (real, public-pre-season) bye collides
with a QB we already drafted, swap to the best-VORP available QB with a distinct
bye. Byes are derived from nflverse (a team's bye = its missing week in 1-14).
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
from ffi.sim.season import evaluate_league
from ffi.sim.strategy import StrategyParams, make_strategy_fn

N_SEEDS = 50
STRAT = StrategyParams(
    scenario="qb_hoard_12",
    qb_by_round=(6, 11, 16),
    qb_not_before=(1, 1, 13),
    qb_tier_targets=(),
)


def player_byes(conn, season):
    """gsis_id -> bye week (its modal team's missing week in 1-14)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT team, array_agg(DISTINCT week) FROM raw.nflverse_player_week "
        "WHERE season=%s AND week BETWEEN 1 AND 14 GROUP BY team",
        (season,),
    )
    team_bye = {}
    for team, weeks in cur.fetchall():
        missing = [w for w in range(1, 15) if w not in set(weeks)]
        if len(missing) == 1:
            team_bye[team] = missing[0]
    cur.execute(
        "SELECT gsis_id, team, count(*) FROM raw.nflverse_player_week "
        "WHERE season=%s AND position='QB' GROUP BY gsis_id, team",
        (season,),
    )
    modal, best = {}, {}
    for gsis, team, n in cur.fetchall():
        if n > best.get(gsis, 0):
            best[gsis], modal[gsis] = n, team
    return {g: team_bye[t] for g, t in modal.items() if t in team_bye}


def make_bye_aware_fn(strat, bye):
    """Wrap the strategy: swap a colliding QB pick for the best-VORP available
    QB with a bye distinct from our already-drafted QBs."""
    base = make_strategy_fn(strat)
    ours = []

    def fn(avail_by_pos, round_, counts, picks_left_after):
        pick = base(avail_by_pos, round_, counts, picks_left_after)
        if pick.position == "QB":
            taken_byes = {bye.get(p.ref) for p in ours if p.position == "QB"}
            taken_byes.discard(None)
            if bye.get(pick.ref) in taken_byes:
                alts = sorted(
                    (
                        q
                        for q in avail_by_pos["QB"]
                        if bye.get(q.ref) not in taken_byes
                        and bye.get(q.ref) is not None
                    ),
                    key=lambda q: q.vorp,
                    reverse=True,
                )
                if alts:
                    pick = alts[0]
        ours.append(pick)
        return pick

    return fn, ours


def our_qbs(picks, our_pos):
    return sorted(
        (p for p in picks if p["position_slot"] == our_pos and p["pos"] == "QB"),
        key=lambda p: p["overall"],
    )


def run_condition(conn, priors, pools, lookups, byes, bye_aware):
    pct, top3, collide = [], [], []
    for season in BACKTEST_SEASONS:
        pool, lookup, bye = pools[season], lookups[season], byes[season]
        for i in range(N_SEEDS):
            seed = 700_000 + season * 100 + i
            if bye_aware:
                pick_fn, _ = make_bye_aware_fn(STRAT, bye)
            else:
                pick_fn = make_strategy_fn(STRAT)
            res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
            standings = evaluate_league(
                res.rosters, cv_by_pos={}, seed=seed, points_lookup=lookup
            )
            our = standings[res.our_position]
            pct.append(our)
            top3.append((sum(1 for v in standings.values() if v > our) + 1) <= 3)
            qb_byes = [
                bye.get(p.get("ref")) for p in our_qbs(res.picks, res.our_position)
            ]
            known = [b for b in qb_byes if b is not None]
            collide.append(any(v > 1 for v in Counter(known).values()))
    se2 = 2 * statistics.stdev(pct) / (len(pct) ** 0.5)
    return statistics.mean(pct), se2, statistics.mean(top3), statistics.mean(collide)


def main():
    conn = connect()
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}
    byes = {s: player_byes(conn, s) for s in BACKTEST_SEASONS}

    print(
        f"{'condition':>12} {'all-play%':>10} {'2SE':>6} {'top3%':>6} {'QB-bye-collision%':>18}"
    )
    for label, aware in (("bye-blind", False), ("bye-aware", True)):
        m, se, t3, col = run_condition(conn, priors, pools, lookups, byes, aware)
        print(f"{label:>12} {m:>9.1%} {se:>5.1%} {t3:>5.0%} {col:>17.0%}")


if __name__ == "__main__":
    main()
