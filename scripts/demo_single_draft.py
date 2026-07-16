#!/usr/bin/env python3
"""Run a single simulated draft and show results: our roster, all rosters,
and a Monte Carlo season evaluation. Quick demo — ~5 seconds runtime.

    uv run python scripts/demo_single_draft.py [--seed N] [--position P]

Defaults: seed=42, our_position=5 (middle of the 1st round), scenario=qb_hoard_12.
"""
import argparse

from ffi.db import connect
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.sim.strategy import DEPLOYED_PARAMS, StrategyParams, make_strategy_fn
from ffi.sim.draft import run_draft, TEAMS, ROUNDS
from ffi.sim.season import evaluate_league, fit_weekly_points_cv


def _round_from_overall(overall: int) -> int:
    return (overall - 1) // TEAMS + 1


def main():
    ap = argparse.ArgumentParser(description="Run a single sim draft and show results")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--position", type=int, default=5, help="our draft position 1-12")
    ap.add_argument("--scenario", default="qb_hoard_12")
    ap.add_argument(
        "--n-seasons", type=int, default=50, help="MC seasons for evaluation"
    )
    ap.add_argument(
        "--default",
        action="store_true",
        help="use bare StrategyParams() defaults (front-QB3 / TE3) instead of the "
        "live-deployed strategy -- for A/B contrast only",
    )
    args = ap.parse_args()

    conn = connect()
    print("Loading board (pool + priors + valuation)...")
    pool = build_pool(conn, args.scenario)
    priors = build_slot_priors(conn)
    print(f"  Pool: {len(pool)} players, priors: latest_season={priors.latest_season}")

    # Default: show what the LIVE assistant actually drafts (DEPLOYED_PARAMS --
    # QB3 late + TE cap 2). --default drops back to the bare defaults so the two
    # can be compared side by side. Anchoring on DEPLOYED_PARAMS is what stops the
    # demo from misrepresenting the shipped strategy.
    params = StrategyParams() if args.default else DEPLOYED_PARAMS
    label = (
        "DEFAULT (front-QB3/TE3)" if args.default else "DEPLOYED (QB3-late/TE-cap-2)"
    )
    print(f"  Strategy: {label}")
    fn = make_strategy_fn(params)
    print(
        f"\nRunning draft (seed={args.seed}, our_position={args.position}, "
        f"scenario={args.scenario})..."
    )
    result = run_draft(
        pool,
        priors,
        fn,
        seed=args.seed,
        our_franchise_slot=12,
        our_position=args.position,
    )

    total_picks = len(result.picks)
    print(f"  Draft complete: {total_picks} picks, {ROUNDS} rounds")

    # IMPORTANT: result.rosters is keyed by DRAFT POSITION (1-12), not franchise slot.
    # Our draft position is args.position. result.our_position is the resolved position.
    our_pos = result.our_position
    our_roster = result.rosters[our_pos]
    our_pick_dicts = [p for p in result.picks if p["franchise_slot"] == 12]

    print(f"\n{'='*70}")
    print(
        f"OUR ROSTER (position {our_pos}, franchise slot 12) — {len(our_roster)} picks"
    )
    print(f"{'='*70}")

    by_pos = {}
    for i, player in enumerate(our_roster):
        overall = our_pick_dicts[i]["overall"]
        r = _round_from_overall(overall)
        by_pos.setdefault(player.position, []).append((r, player))

    for pos in ["QB", "RB", "WR", "TE", "K", "DEF"]:
        if pos in by_pos:
            for r, p in sorted(by_pos[pos]):
                tier_str = f" tier {p.tier}" if p.tier else ""
                adp_str = f"  adp {p.adp:.0f}" if p.adp else ""
                print(
                    f"  {pos:>3}  R{r:>2}  {p.name:<25}{tier_str}{adp_str}  vorp {p.vorp:.1f}"
                )

    # All 12 teams (first 5 rounds)
    print(f"\n{'='*70}")
    print("ALL TEAMS (first 5 rounds)")
    print(f"{'='*70}")
    for slot in range(1, TEAMS + 1):
        slot_picks = sorted(
            [p for p in result.picks if p["franchise_slot"] == slot],
            key=lambda p: p["overall"],
        )
        names = []
        for p in slot_picks[:5]:
            r = _round_from_overall(p["overall"])
            names.append(f"R{r}{p['pos'][0]}:{p['name'].split()[-1]}")
        marker = " <-- US" if slot == 12 else ""
        print(f"  Slot {slot:>2}: {' | '.join(names)}{marker}")

    # Season evaluation — keyed by draft position (1-12), not franchise slot
    print(f"\n{'='*70}")
    print(f"SEASON EVALUATION ({args.n_seasons} Monte Carlo seasons)")
    print(f"{'='*70}")
    cv_by_pos = fit_weekly_points_cv(conn)
    print(f"  CV by position: {cv_by_pos}")
    print(f"  Running {args.n_seasons} MC seasons...")
    eval_result = evaluate_league(
        result.rosters, cv_by_pos=cv_by_pos, seed=args.seed, n_seasons=args.n_seasons
    )

    # eval_result is dict[int, float] — draft position -> mean all-play win pct
    if isinstance(eval_result, dict):
        print(f"\n  {'Pos':>4}  {'All-play%':>9}  {'Rank':>4}")
        print(f"  {'----':>4}  {'---------':>9}  {'----':>4}")
        sorted_slots = sorted(eval_result.items(), key=lambda x: -x[1])
        for rank, (pos, pct) in enumerate(sorted_slots, 1):
            marker = " ***" if pos == our_pos else ""
            print(f"  {pos:>4}  {pct:>8.1%}  {rank:>4}{marker}")

    # Draft decision summary
    print(f"\n{'='*70}")
    print("DRAFT DECISION ANALYSIS")
    print(f"{'='*70}")
    pos_counts = {}
    for i, player in enumerate(our_roster):
        overall = our_pick_dicts[i]["overall"]
        r = _round_from_overall(overall)
        pos_counts[player.position] = pos_counts.get(player.position, 0) + 1
        tier_str = f" tier {player.tier}" if player.tier else ""
        print(f"  R{r:>2}: {player.name:<25} ({player.position}){tier_str}")

    print(f"\n  Position counts: {pos_counts}")
    print(f"  Starters needed: QB=2 RB=2 WR=3 TE=1 FLEX=1 K=1 DEF=1")

    # What was available when we made our last pick?
    last_overall = max(p["overall"] for p in our_pick_dicts)
    remaining = [p for p in result.picks if p["overall"] > last_overall][:5]
    if remaining:
        print(f"\n  Top 5 VORP players still on the board when we made our last pick:")
        for p in remaining:
            r = _round_from_overall(p["overall"])
            print(f"    R{r} (slot {p['franchise_slot']}): {p['name']} ({p['pos']})")

    print(f"\nDone. Try different seeds or positions:")
    print(
        f"  uv run python scripts/demo_single_draft.py --seed {args.seed + 1} --position {args.position}"
    )
    print(
        f"  uv run python scripts/demo_single_draft.py --seed {args.seed} --position 1  (early pick)"
    )
    print(
        f"  uv run python scripts/demo_single_draft.py --seed {args.seed} --position 12 (late pick)"
    )
    conn.close()


if __name__ == "__main__":
    main()
