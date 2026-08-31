#!/usr/bin/env python3
"""ADR Precondition P4: the waiver-policy ceiling test (R24).

Question: how much playoff probability is reachable from in-season waiver
moves AT ALL? The design doc's "draft 40% / in-season 60%" split is
asserted, never measured. If PERFECT FORESIGHT — knowing every player's
exact weekly score in advance — is worth under 8 playoff-probability points,
then a realistic advisor is worth a fraction of that, and Plans 3-4 (waiver
advisor, trade angles) are not worth 60-80 hours of a 14-week season.

Deliberately a THIN harness: drafts, pools, weekly points, opponent models
and the lineup slot config all come from ffi.sim. The only things
implemented here are the waiver policy itself and the top-6 ranking.

Method, per (season, seed):
  1. run_draft() -> 12 rosters, ours at OUR_FRANCHISE_SLOT.
  2. Baseline: all 14 weeks with the drafted rosters, rank by all-play win
     pct; playoff = rank <= PLAYOFF_TEAMS.
  3. Foresight: same, except OUR roster may add up to WEEKLY_ADD_LIMIT
     players per week (SEASON_ADD_LIMIT for the season) with perfect
     knowledge of that week's points.
  4. delta_pp = 100 * (P(playoff | foresight) - P(playoff | baseline)).

The ceiling is deliberately GENEROUS: opponents never transact, waiver
priority does not exist, and every claim succeeds. A generous ceiling is the
right shape for a scope gate — if even this is small, the realistic number
is smaller.

WEEK-ACCURATE SCORING (do not "simplify" this away). Our team is scored each
week with the roster we ACTUALLY HELD that week (`perfect_foresight_weekly`
returns a snapshot per week; `all_play_pct` takes them via `weekly_rosters`).
Scoring the FINAL end-of-season roster across all 14 weeks instead — the
obvious shortcut, since the policy already returns a final roster — silently
credits a week-12 pickup for weeks 1-11 AND retroactively erases a week-2 drop
from week 1. That is not a generous ceiling, it is a WRONG one in both
directions at once: measured live at Task 10 time it turned the oracle
NEGATIVE in 2024 (-12.5pp) and made 34% of drafts worse than doing nothing,
which is impossible for a true oracle. Week-accurate, the same policy is
+29.07pp (95% CI [+26.28, +31.85], n_cells=1500 = 3 gate seasons x 500 drafts)
and misses the playoffs in 0 of those 1500 drafts.

NOT a monotonicity proof. The greedy policy takes a week-w lineup gain in
exchange for a PERMANENT drop, so it is not provably monotone — a drop made for
week 3 can cost points in week 9. The support for "an oracle never loses ground"
is empirical only: all-play win pct was lowered in 0 of those same 1500 drafts,
min per-draft all-play delta +0.0779. Treat a future negative as a bug report
about the evaluation, not as a violated theorem. Every one of those per-cell
numbers is re-derivable from `--dump-cells` output, retained at
`logs/waiver-ceiling-2026-08-31-fixed-n500-cells.json` (tracked; `logs/` is
otherwise gitignored). See
`tests/test_waiver_ceiling.py::test_an_add_is_not_credited_retroactively` and
`::test_run_season_scores_our_team_week_accurately`.
"""
import argparse
import json
import statistics

from ffi.db import connect
from ffi.sim.backtest import (
    GATE_SEASONS,
    OUR_FRANCHISE_SLOT,
    REF_STRATEGIES,
    cell_base_seed,
    load_backtest_pool,
    load_points_lookup,
)
from ffi.sim.draft import run_draft
from ffi.sim.opponent import STARTERS
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import FLEX_POS, REG_WEEKS
from ffi.sim.strategy import make_strategy_fn

# league_rules.md: "Playoff Teams: 6" of 12.
PLAYOFF_TEAMS = 6
# league_rules.md: "Weekly Acquisitions: Maximum 5", "Season Acquisitions: Maximum 65".
WEEKLY_ADD_LIMIT = 5
SEASON_ADD_LIMIT = 65
# Only the top free agents by that week's actual points can possibly help.
FA_CANDIDATES_PER_WEEK = 25
# The ceiling is measured against the strategy the D7 reference gate uses.
CEILING_STRATEGY_IDX = 0
CUT_THRESHOLD_PP = 8.0


def _points(player, week: int, lookup: dict) -> float:
    """Weekly points. gsis_id=None (DEF in the backtest representation)
    ALWAYS scores 0.0 — the same hardcoded contract as ffi.sim.season."""
    if player.gsis_id is None:
        return 0.0
    return lookup.get((player.gsis_id, week), 0.0)


def lineup_total(roster, week: int, lookup: dict) -> float:
    """Optimal lineup total for one team-week: STARTERS by position plus the
    best leftover RB/WR/TE as FLEX. Scalar mirror of
    ffi.sim.season._lineup_total; tests/test_waiver_ceiling.py asserts the
    two agree exactly."""
    by_pos: dict[str, list[float]] = {}
    for p in roster:
        by_pos.setdefault(p.position, []).append(_points(p, week, lookup))
    total = 0.0
    leftovers: list[float] = []
    for pos, need in STARTERS.items():
        values = sorted(by_pos.get(pos, []), reverse=True)
        total += sum(values[:need])
        if pos in FLEX_POS:
            leftovers.extend(values[need:])
    if leftovers:
        total += max(leftovers)
    return total


def all_play_pct(
    rosters: dict, lookup: dict, weekly_rosters: dict | None = None
) -> dict:
    """team -> mean all-play win pct over REG_WEEKS. A team beats every other
    team with a STRICTLY lower total that week (ties count for neither
    side), matching ffi.sim.season's convention.

    `weekly_rosters` maps team -> {week: roster held that week} for teams whose
    roster CHANGES mid-season (i.e. ours, under the waiver policy). Teams absent
    from it are static all season and scored from `rosters`. Omit it entirely
    and this is exactly `ffi.sim.season.evaluate_league`'s static behaviour,
    which `test_lineup_total_matches_the_library_evaluator` pins."""
    weekly_rosters = weekly_rosters or {}
    teams = sorted(rosters)
    totals = {}
    for t in teams:
        held = weekly_rosters.get(t)
        totals[t] = [
            lineup_total(held[w] if held is not None else rosters[t], w, lookup)
            for w in range(1, REG_WEEKS + 1)
        ]
    denom = (len(teams) - 1) * REG_WEEKS
    out = {}
    for t in teams:
        wins = sum(
            1
            for w in range(REG_WEEKS)
            for other in teams
            if other != t and totals[t][w] > totals[other][w]
        )
        out[t] = wins / denom
    return out


def _droppable(roster, counts: dict) -> list:
    """Players whose removal keeps every position at its starter minimum."""
    return [p for p in roster if counts[p.position] - 1 >= STARTERS.get(p.position, 0)]


def perfect_foresight_weekly(
    roster,
    fa_pool,
    lookup: dict,
    weekly_limit: int = WEEKLY_ADD_LIMIT,
    season_limit: int = SEASON_ADD_LIMIT,
):
    """({week: roster HELD that week}, total_adds) under perfect weekly foresight.

    Greedy per week: drop the player with the lowest REMAINING-season points
    (weeks w..14) among those droppable without breaching a starter minimum,
    add the free agent that maximizes THIS week's lineup total. Stop when no
    swap helps. Adds are permanent, as in a real league.

    The snapshot for week w is taken AFTER that week's adds and before week
    w+1's, so a player picked up in week 12 appears in weeks 12-14 only. This
    is what makes the reported number a real ceiling rather than a hindsight
    artifact — see the module docstring.
    """
    current = list(roster)
    available = list(fa_pool)
    adds = 0
    held: dict[int, list] = {}
    for week in range(1, REG_WEEKS + 1):
        for _ in range(weekly_limit):
            if adds >= season_limit or not available:
                break
            counts: dict[str, int] = {}
            for p in current:
                counts[p.position] = counts.get(p.position, 0) + 1
            candidates_out = _droppable(current, counts)
            if not candidates_out:
                break
            remaining = {
                id(p): sum(_points(p, w, lookup) for w in range(week, REG_WEEKS + 1))
                for p in candidates_out
            }
            drop = min(candidates_out, key=lambda p: remaining[id(p)])
            base = lineup_total(current, week, lookup)
            without = [p for p in current if p is not drop]
            candidates_in = sorted(
                available, key=lambda p: _points(p, week, lookup), reverse=True
            )[:FA_CANDIDATES_PER_WEEK]
            best_gain, best_add = 0.0, None
            for candidate in candidates_in:
                gain = lineup_total(without + [candidate], week, lookup) - base
                if gain > best_gain:
                    best_gain, best_add = gain, candidate
            if best_add is None:
                break
            current = without + [best_add]
            available = [p for p in available if p is not best_add]
            adds += 1
        held[week] = list(current)
    return held, adds


def perfect_foresight_roster(
    roster,
    fa_pool,
    lookup: dict,
    weekly_limit: int = WEEKLY_ADD_LIMIT,
    season_limit: int = SEASON_ADD_LIMIT,
):
    """(roster_after_the_season, total_adds) — the final week's snapshot.

    Kept as the roster-level view of `perfect_foresight_weekly` because the
    add-limit and starter-minimum invariants are properties of the roster, not
    of the schedule, and the tests assert them there. Scoring a season with
    THIS return value is the retroactive-credit trap the module docstring
    warns about: use `perfect_foresight_weekly` for anything week-indexed.
    """
    held, adds = perfect_foresight_weekly(
        roster, fa_pool, lookup, weekly_limit, season_limit
    )
    return held[REG_WEEKS], adds


def run_season(conn, priors, season: int, n_drafts: int) -> list:
    pool = load_backtest_pool(conn, season)
    lookup = load_points_lookup(conn, season)
    pick_fn = make_strategy_fn(REF_STRATEGIES[CEILING_STRATEGY_IDX])
    base_seed = cell_base_seed(CEILING_STRATEGY_IDX, season)
    cells = []
    for i in range(n_drafts):
        seed = base_seed + i
        result = run_draft(
            pool, priors, pick_fn, seed=seed, our_franchise_slot=OUR_FRANCHISE_SLOT
        )
        ours = result.our_position
        rostered = {p.ref for roster in result.rosters.values() for p in roster}
        fa_pool = [p for p in pool if p.ref not in rostered and p.gsis_id is not None]

        base_pct = all_play_pct(result.rosters, lookup)
        base_rank = sorted(base_pct, key=lambda t: -base_pct[t]).index(ours) + 1

        held, adds = perfect_foresight_weekly(result.rosters[ours], fa_pool, lookup)
        # Opponents never transact, so only OUR team needs a weekly view.
        fs_pct = all_play_pct(result.rosters, lookup, weekly_rosters={ours: held})
        fs_rank = sorted(fs_pct, key=lambda t: -fs_pct[t]).index(ours) + 1

        cells.append(
            {
                "season": season,
                "seed": seed,
                "adds": adds,
                "base_pct": base_pct[ours],
                "foresight_pct": fs_pct[ours],
                "base_playoff": base_rank <= PLAYOFF_TEAMS,
                "foresight_playoff": fs_rank <= PLAYOFF_TEAMS,
            }
        )
    return cells


def summarize(cells: list) -> dict:
    """Playoff-probability delta and its McNemar CI over ANY subset of cells.

    Shared by the headline and by the per-season breakdown so the two can never
    be computed by different rules. Playoff probability saturates at 1.0 for the
    oracle, so `mean_base_pct`/`mean_foresight_pct` (all-play win pct, which does
    not saturate) are reported alongside as the readable-signal view.
    """
    n = len(cells)
    if not n:
        raise ValueError("summarize() got zero cells — nothing was simulated.")
    base_p = sum(c["base_playoff"] for c in cells) / n
    fs_p = sum(c["foresight_playoff"] for c in cells) / n
    delta_pp = 100 * (fs_p - base_p)
    # McNemar: only discordant pairs carry information about a paired
    # difference of proportions.
    b = sum(1 for c in cells if c["foresight_playoff"] and not c["base_playoff"])
    c_ = sum(1 for c in cells if c["base_playoff"] and not c["foresight_playoff"])
    se_pp = 100 * ((b + c_) ** 0.5) / n
    return {
        "n_cells": n,
        "mean_adds": round(statistics.mean(c["adds"] for c in cells), 2),
        "baseline_playoff_prob": round(base_p, 4),
        "foresight_playoff_prob": round(fs_p, 4),
        "delta_pp": round(delta_pp, 2),
        "se_pp": round(se_pp, 2),
        "ci95_pp": [round(delta_pp - 2 * se_pp, 2), round(delta_pp + 2 * se_pp, 2)],
        "mean_base_pct": round(statistics.mean(c["base_pct"] for c in cells), 4),
        "mean_foresight_pct": round(
            statistics.mean(c["foresight_pct"] for c in cells), 4
        ),
        "min_allplay_delta": round(
            min(c["foresight_pct"] - c["base_pct"] for c in cells), 4
        ),
        "n_allplay_lowered": sum(
            1 for c in cells if c["foresight_pct"] < c["base_pct"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seasons",
        default=",".join(str(s) for s in GATE_SEASONS),
        help="comma-separated; pools must exist in sim.backtest_pool",
    )
    parser.add_argument("--n-drafts", type=int, default=40)
    parser.add_argument(
        "--dump-cells",
        metavar="PATH",
        help="write every per-cell dict to PATH as JSON. Without this the run "
        "leaves only aggregates, and a per-season or per-draft claim about a "
        "past run cannot be re-derived from the retained artifact.",
    )
    args = parser.parse_args()

    conn = connect()
    priors = build_slot_priors(conn)
    cells = []
    for season in [int(s) for s in args.seasons.split(",")]:
        cells += run_season(conn, priors, season, args.n_drafts)

    seasons = sorted({c["season"] for c in cells})
    summary = summarize(cells)
    summary["seasons"] = seasons
    summary["n_drafts_per_season"] = args.n_drafts
    summary["cut_threshold_pp"] = CUT_THRESHOLD_PP
    summary["decision"] = (
        "BUILD Plans 3-4"
        if summary["delta_pp"] >= CUT_THRESHOLD_PP
        else "CUT Plans 3-4"
    )
    summary["per_season"] = {
        str(s): summarize([c for c in cells if c["season"] == s]) for s in seasons
    }
    print(json.dumps(summary, indent=2))

    print("\nPER SEASON (all-play does not saturate; playoff prob does):")
    for s in seasons:
        ps = summary["per_season"][str(s)]
        print(
            f"  {s}  n={ps['n_cells']:<5} playoff {ps['baseline_playoff_prob']:.3f} -> "
            f"{ps['foresight_playoff_prob']:.3f} ({ps['delta_pp']:+.2f}pp)   "
            f"all-play {ps['mean_base_pct']:.4f} -> {ps['mean_foresight_pct']:.4f}   "
            f"min all-play delta {ps['min_allplay_delta']:+.4f}   "
            f"lowered {ps['n_allplay_lowered']}/{ps['n_cells']}"
        )

    if args.dump_cells:
        with open(args.dump_cells, "w") as fh:
            json.dump({"summary": summary, "cells": cells}, fh, indent=2)
        print(f"\nwrote {len(cells)} cells -> {args.dump_cells}")

    print(
        f"\nCEILING: perfect-foresight waivers are worth {summary['delta_pp']:+.2f}pp of "
        f"playoff probability (95% CI {summary['ci95_pp'][0]:+.2f} to "
        f"{summary['ci95_pp'][1]:+.2f}, n={summary['n_cells']}). "
        f"Threshold {CUT_THRESHOLD_PP}pp -> {summary['decision']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
