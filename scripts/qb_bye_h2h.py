#!/usr/bin/env python3
"""The RIGHT test for the bye hedge: H2H week-by-week, not season all-play.

All-play % is expectation-linear in points, so it CANNOT see variance reduction
-- which is exactly what avoiding a shared-QB-bye does (it converts one
catastrophic ~25-pt week into a normal one). H2H W/L is a binary threshold per
week, so a bye-hole week that would flip a matchup into a loss shows up here.

Runs the delayed strategy (qb_not_before=(1,1,13)) bye-blind vs bye-aware, on
the SAME seeds (same opponents), builds a 12-team round-robin schedule, and
reports: our bye-hole weeks (weeks we can't field 2 QBs), avg H2H wins, avg
placement, and playoff-make rate (top-6). Graded on actual nflverse points.
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
)
from ffi.sim.strategy import StrategyParams, make_strategy_fn

N_SEEDS = 50
STRAT = StrategyParams(
    scenario="qb_hoard_12",
    qb_by_round=(6, 11, 16),
    qb_not_before=(1, 1, 13),
    qb_tier_targets=(),
)


def player_byes(conn, season):
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
    base = make_strategy_fn(strat)
    ours = []

    def fn(avail_by_pos, round_, counts, picks_left_after):
        pick = base(avail_by_pos, round_, counts, picks_left_after)
        if pick.position == "QB":
            taken = {bye.get(p.ref) for p in ours if p.position == "QB"}
            taken.discard(None)
            if bye.get(pick.ref) in taken:
                alts = sorted(
                    (
                        q
                        for q in avail_by_pos["QB"]
                        if bye.get(q.ref) not in taken and bye.get(q.ref)
                    ),
                    key=lambda q: q.vorp,
                    reverse=True,
                )
                if alts:
                    pick = alts[0]
        ours.append(pick)
        return pick

    return fn


def round_robin(n):
    teams, rounds = list(range(n)), []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


ROUNDS = round_robin(12)  # 11 rounds; weeks 12-14 reuse rounds 0-2


def weekly_totals(rosters, lookup):
    tk, flat, pos_idx = _build_index(rosters)
    pts = _lookup_weekly_points(flat, lookup)
    return tk, {t: _lineup_total(pts, pos_idx[t])[0] for t in tk}


def h2h_wins(tk, wt):
    """tk sorted team keys (draft positions); wt[t] = (REG_WEEKS,). Returns
    {team: win_count} over a 14-week round-robin (idx into tk via schedule)."""
    wins = {t: 0 for t in tk}
    for w in range(REG_WEEKS):
        pairs = ROUNDS[w % len(ROUNDS)]
        for a, b in pairs:
            ta, tb = tk[a], tk[b]
            if wt[ta][w] >= wt[tb][w]:
                wins[ta] += 1
            else:
                wins[tb] += 1
    return wins


def qb_hole_weeks(roster, lookup):
    qbs = [p for p in roster if p.position == "QB" and p.gsis_id]
    return sum(
        1
        for w in range(1, REG_WEEKS + 1)
        if sum(1 for q in qbs if lookup.get((q.gsis_id, w), 0.0) > 0) < 2
    )


def run(conn, priors, pools, lookups, byes, bye_aware):
    holes, wins, place, playoff = [], [], [], []
    for season in BACKTEST_SEASONS:
        pool, lookup, bye = pools[season], lookups[season], byes[season]
        for i in range(N_SEEDS):
            seed = 600_000 + season * 100 + i
            pick_fn = (
                make_bye_aware_fn(STRAT, bye) if bye_aware else make_strategy_fn(STRAT)
            )
            res = run_draft(pool, priors, pick_fn, seed=seed, our_franchise_slot=12)
            tk, wt = weekly_totals(res.rosters, lookup)
            w = h2h_wins(tk, wt)
            our = res.our_position
            rank = sum(1 for t in tk if w[t] > w[our]) + 1
            wins.append(w[our])
            place.append(rank)
            playoff.append(rank <= 6)
            holes.append(qb_hole_weeks(res.rosters[our], lookup))
    return (
        statistics.mean(holes),
        statistics.mean(wins),
        statistics.mean(place),
        statistics.mean(playoff),
    )


def main():
    conn = connect()
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}
    byes = {s: player_byes(conn, s) for s in BACKTEST_SEASONS}

    print(
        f"{'condition':>11} {'bye-hole wks':>12} {'H2H wins':>9} {'avg place':>10} {'playoff%':>9}"
    )
    for label, aware in (("bye-blind", False), ("bye-aware", True)):
        h, w, p, po = run(conn, priors, pools, lookups, byes, aware)
        print(f"{label:>11} {h:>12.2f} {w:>9.2f} {p:>10.2f} {po:>8.0%}")


if __name__ == "__main__":
    main()
