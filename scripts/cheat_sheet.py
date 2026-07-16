#!/usr/bin/env python3
"""Draft-day cheat sheet under OUR league scoring (incompletion-fixed board).

Positional tier boards + a roster-construction playbook, so it can be
cross-referenced live during an FP mock (which isn't Yahoo-API-visible, so the
live assistant can't follow it). Positional (not one cross-position VORP list)
on purpose: raw VORP over-ranks QBs because it ignores P(a player ever starts) --
the playbook encodes that judgment instead. Writes reports/cheat-sheet-<date>.md.

Usage: uv run python scripts/cheat_sheet.py [--date YYYY-MM-DD] [--scenario S]
"""
import argparse
import datetime

from ffi.db import connect
from ffi.sim.pool import build_pool

# how deep to list each position (starters*12 + useful bench depth)
DEPTH = {"QB": 28, "RB": 48, "WR": 55, "TE": 22, "K": 14, "DEF": 16}

PLAYBOOK = """\
## Game plan (our scoring — 2QB, full-PPR, 6pt pass TD, +1 first downs)

Roster targets: **3 QB** (start 2, QB3 = insurance) · **2 RB start** · **3 WR start**
· **TE 2** (start 1 + 1 backup) · **1 FLEX** · **1 K** · **1 DEF**.

- **RB — SCARCE this year. Steep cliff (RB12→RB24 ≈ −82 pts vs WR −33).** Prioritize
  RB early; secure 2 startable + depth *before* the cliff. This is the biggest edge.
- **WR — deep & flat.** You can wait; load WR in the mid rounds. Don't take a WR over
  a same-tier RB early.
- **QB — high value but the startable tier is DEEP (QB2≈QB20 in our scoring).** Get 2
  startable QBs but DON'T burn premium picks on QB2 — a startable QB is there in the
  mid rounds. **QB3 very late (R10+); it only starts on bye/injury weeks.**
- **TE — 1 starter + exactly 1 backup (roster 2).** We start 1 TE, so a 3rd TE never
  plays — don't reach for TE3.
- **K / DEF — last two rounds only.** Never earlier.
- **Edge check:** FP's national ADP doesn't know our scoring. Compare our rank vs ADP —
  a player whose ADP is far *later* than our rank is a value; far *earlier* is a reach.
  (VORP over-states QB3/TE3 depth — trust the playbook over raw VORP at those slots.)
"""


def board(pool, pos, n):
    ps = sorted((p for p in pool if p.position == pos), key=lambda p: -p.proj_points)[
        :n
    ]
    lines = [
        f"### {pos}",
        "",
        "| # | player | proj | VORP | tier | ADP |",
        "|--:|---|--:|--:|:--:|--:|",
    ]
    last_tier = None
    for i, p in enumerate(ps, 1):
        if last_tier is not None and p.tier != last_tier:
            lines.append("| | *— tier break —* | | | | |")
        last_tier = p.tier
        adp = f"{p.adp:.0f}" if p.adp is not None else "—"
        lines.append(
            f"| {i} | {p.name} | {p.proj_points:.0f} | {p.vorp:+.0f} | {p.tier} | {adp} |"
        )
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--scenario", default="qb_hoard_12")
    args = ap.parse_args()
    date = args.date or datetime.date.today().isoformat()

    conn = connect()
    pool = build_pool(conn, args.scenario)

    out = [
        f"# Draft Cheat Sheet — {date}",
        f"*Our league scoring · incompletion-fixed board · scenario {args.scenario}*",
        "",
        PLAYBOOK,
        "",
    ]
    for pos in (
        "RB",
        "WR",
        "QB",
        "TE",
        "DEF",
        "K",
    ):  # RB/WR first: draft-priority order
        out.append(board(pool, pos, DEPTH[pos]))

    path = f"reports/cheat-sheet-{date}.md"
    with open(path, "w") as f:
        f.write("\n".join(out))
    print(f"wrote {path}  ({sum(1 for _ in pool)} players in pool)")
    # console preview: RB + QB tops
    print("\n--- preview: RB top 15 ---")
    for i, p in enumerate(
        sorted((p for p in pool if p.position == "RB"), key=lambda p: -p.proj_points)[
            :15
        ],
        1,
    ):
        adp = f"{p.adp:.0f}" if p.adp is not None else "—"
        print(
            f"  RB{i:<2} {p.name:<22} proj {p.proj_points:5.0f}  vorp {p.vorp:+5.0f}  t{p.tier}  adp {adp}"
        )
    print("--- preview: QB top 12 ---")
    for i, p in enumerate(
        sorted((p for p in pool if p.position == "QB"), key=lambda p: -p.proj_points)[
            :12
        ],
        1,
    ):
        adp = f"{p.adp:.0f}" if p.adp is not None else "—"
        print(
            f"  QB{i:<2} {p.name:<22} proj {p.proj_points:5.0f}  vorp {p.vorp:+5.0f}  t{p.tier}  adp {adp}"
        )


if __name__ == "__main__":
    main()
