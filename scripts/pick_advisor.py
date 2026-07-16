#!/usr/bin/env python3
"""Live mock-draft advisor: given who's been drafted so far (read off a
screenshot) and my own roster, run the DEPLOYED strategy engine against the
board and say who to take next -- the same evaluate_rules/DEPLOYED_PARAMS logic
the live Aug-29 assistant uses, so mock advice == real-draft advice.

    uv run python scripts/pick_advisor.py \
        --mine "Jalen Hurts,Saquon Barkley" \
        --taken "Josh Allen,Lamar Jackson,...,Jalen Hurts,...,Saquon Barkley"

overall pick = len(taken)+1; round derived for a 12-team snake. Prints the
engine's pick + rule, the top rule-4 value candidates, and best-available by
position for context.
"""
import argparse

from ffi.db import connect
from ffi.sim.draft import ROUNDS, TEAMS
from ffi.sim.pool import build_pool
from ffi.sim.strategy import DEPLOYED_PARAMS, evaluate_rules, rule4_candidates


def match_ref(pool, name):
    lo = name.strip().lower()
    exact = [p for p in pool if p.name.lower() == lo]
    if exact:
        return exact[0].ref
    last = lo.split()[-1]
    first = lo.split()[0]
    cands = [p for p in pool if last in p.name.lower() and first[:4] in p.name.lower()]
    if not cands:
        cands = [p for p in pool if p.name.lower().split()[-1] == last]
    return max(cands, key=lambda p: p.proj_points).ref if cands else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--taken", default="", help="comma-sep names drafted so far (incl. mine)"
    )
    ap.add_argument("--mine", default="", help="comma-sep names on MY roster")
    ap.add_argument("--scenario", default="qb_hoard_12")
    args = ap.parse_args()

    conn = connect()
    pool = build_pool(conn, args.scenario)

    taken_names = [n for n in args.taken.split(",") if n.strip()]
    mine_names = [n for n in args.mine.split(",") if n.strip()]
    taken_refs, unmatched = set(), []
    for n in taken_names:
        r = match_ref(pool, n)
        (taken_refs.add(r) if r else unmatched.append(n))
    mine_refs = {match_ref(pool, n) for n in mine_names}
    mine_refs.discard(None)

    overall = len(taken_names) + 1
    round_ = (overall - 1) // TEAMS + 1
    picks_left_after = ROUNDS - round_

    counts = {}
    for p in pool:
        if p.ref in mine_refs:
            counts[p.position] = counts.get(p.position, 0) + 1

    avail = [p for p in pool if p.ref not in taken_refs]
    avail_by_pos = {}
    for p in avail:  # pool is already sorted (adp asc, none last)
        avail_by_pos.setdefault(p.position, []).append(p)

    print(
        f"=== pick #{overall}  (round {round_}, slot 8)  my roster: {counts or '(empty)'} ==="
    )
    if unmatched:
        print(f"!! unmatched taken names (fix spelling): {unmatched}")

    pick, rule = evaluate_rules(
        avail_by_pos, round_, counts, picks_left_after, DEPLOYED_PARAMS
    )
    print(
        f"\n>>> TAKE: {pick.name} ({pick.position})  — rule={rule}  vorp {pick.vorp:+.0f} tier {pick.tier}"
    )

    scored = rule4_candidates(
        avail_by_pos, round_, counts, picks_left_after, DEPLOYED_PARAMS
    )
    scored.sort(key=lambda sp: -sp[0])
    print("\nvalue board (legal rule-4 candidates):")
    for s, p in scored[:8]:
        adp = f"adp {p.adp:.0f}" if p.adp is not None else "adp —"
        print(
            f"  {p.name:<22} {p.position:<3} score {s:+7.0f}  vorp {p.vorp:+6.0f}  t{p.tier}  {adp}"
        )

    print("\nbest available by position:")
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        top = avail_by_pos.get(pos, [])[:3]
        s = "  ".join(f"{p.name}({p.vorp:+.0f})" for p in top)
        print(f"  {pos}: {s}")


if __name__ == "__main__":
    main()
