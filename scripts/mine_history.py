#!/usr/bin/env python3
"""Render the historical mining report (the user-facing Phase 2 deliverable)."""
import datetime
import pathlib

from ffi.db import connect
from ffi.history.mining import (
    all_play,
    champion_value_split,
    draft_slot_outcomes,
    position_round_tendencies,
    qb_timing_by_slot,
    trade_stats,
    transaction_timing,
)

conn = connect()
today = datetime.date.today().isoformat()
L = [
    f"# NAJEE league historical mining — {today}",
    "\n**Coverage:** drafts/standings/transactions/matchups 2010-2025 (16 seasons); "
    "league-scoring player-weeks 2019-2025 only (champions split limited to those 7). "
    "**Slot caveat:** results key on team slots; humans changed within slots "
    "(see manager_slot_annotations — currently only slot 12/Brent/~2022 is annotated).",
    "\n**Week-bucket method:** transaction timestamps are bucketed into approximate NFL "
    "weeks anchored on each season's real week-1 start date (the `week_start` field on "
    "the week=1 raw.yahoo_matchups payload — present and sane for all 16 seasons; spot-"
    "checked 2010/2012/2014/2019/2021/2023/2025, all land in early September). An earlier "
    "draft anchored on the earliest transaction timestamp (draft day) was rejected after "
    "verification: draft day sits ~2-3 weeks before week 1 (confirmed: 2010 draft-day txn "
    "2010-08-24 vs. week-1 start 2010-09-09, a 16-day/2.3-week gap), which would have "
    "shifted every bucket 2-3 weeks late.",
]

with conn.cursor() as cur:
    cur.execute(
        "SELECT league_slot, human_label, from_season, to_season FROM public.manager_slot_annotations ORDER BY 1,3"
    )
    annos = cur.fetchall()
L += [
    "\n## Annotations on file",
    *(f"- slot {s}: {h} ({f}-{t or 'present'})" for s, h, f, t in annos),
]

L += [
    "\n## 1. Draft slot -> outcome (16 seasons)",
    "| slot | seasons | avg finish | titles | avg PF |",
    "|---|---|---|---|---|",
]
for r in draft_slot_outcomes(conn):
    L.append(
        f"| {r['slot']} | {r['seasons']} | {float(r['avg_finish']):.2f} | {r['titles']} | {float(r['avg_pf'] or 0):.0f} |"
    )

L += [
    "\n## 2. QB draft timing by slot (2QB fingerprint)",
    "| slot | QB1 round | QB2 round | QB3 round | seasons |",
    "|---|---|---|---|---|",
]
for r in qb_timing_by_slot(conn):
    L.append(
        f"| {r['slot']} | {float(r['qb1_round'] or 0):.1f} | {float(r['qb2_round'] or 0):.1f} | "
        f"{float(r['qb3_round'] or 0):.1f} | {r['seasons']} |"
    )

L += ["\n## 3. Position-by-round tendencies (share of picks, per slot)"]
tend = position_round_tendencies(conn)
slots = sorted({r["slot"] for r in tend})
for slot in slots:
    rows = [r for r in tend if r["slot"] == slot]
    total = {
        b: sum(r["picks"] for r in rows if r["band"] == b)
        for b in ("R1-3", "R4-8", "R9+")
    }
    line = f"- **slot {slot}**: " + "; ".join(
        f"{b}: "
        + ", ".join(
            f"{r['position']} {100*r['picks']/total[b]:.0f}%"
            for r in sorted(rows, key=lambda r: -r["picks"])
            if r["band"] == b
        )[:80]
        for b in ("R1-3", "R4-8", "R9+")
    )
    L.append(line)

L += [
    "\n## 4. All-play vs record (luck audit; hypothesis 6.2)",
    "Biggest schedule-luck beneficiaries and victims (|luck| = actual% - all-play%):",
    "| season | slot | team | record | all-play% | luck |",
    "|---|---|---|---|---|---|",
]
ap = sorted(all_play(conn), key=lambda r: -abs(r["luck"]))[:15]
for r in ap:
    L.append(
        f"| {r['season']} | {r['slot']} | {r['team']} | {r['actual_w']}-{r['actual_l']} | "
        f"{r['all_play_pct']:.3f} | {r['luck']:+.3f} |"
    )

L += ["\n## 5. Transaction timing (hypothesis 6.3: weeks 10-14 cluster?)"]
tt = transaction_timing(conn)
by_week = {}
for r in tt:
    by_week[r["approx_week"]] = by_week.get(r["approx_week"], 0) + r["n"]
L += ["| approx week | transactions (all seasons) |", "|---|---|"]
L += [f"| {w} | {n} |" for w, n in sorted(by_week.items())]

ts_ = trade_stats(conn)
L += [
    "\n## 6. Trades (hypothesis 5.4)",
    f"- total trades 2010-2025: **{ts_['total']}** "
    f"({ts_['total']/16:.1f}/season); QB involved in {ts_['qb_involved']} "
    f"({100*ts_['qb_involved']/max(ts_['total'],1):.0f}%)",
    "- per season: "
    + ", ".join(f"{s}: {n}" for s, n in sorted(ts_["per_season"].items())),
]

L += [
    "\n## 7. Champions: draft vs waiver value split (2019-2025; hypothesis 1.3)",
    "Attribution: player's weekly league-scoring points credited to the roster holding him "
    "that week (bench/start unknown — lineups not imported). Points require an nflverse "
    "gsis_id crosswalk match — team defenses (DEF) do not join and are undercounted here.",
    "| season | champion | drafted pts | added pts | traded-in pts |",
    "|---|---|---|---|---|",
]
for r in champion_value_split(conn):
    L.append(
        f"| {r['season']} | {r['champion']} | {r.get('draft', 0):.0f} | "
        f"{r.get('add', 0):.0f} | {r.get('trade_in', 0):.0f} |"
    )

out = pathlib.Path(f"docs/research/{today}-historical-mining-report.md")
out.write_text("\n".join(L) + "\n")
print(f"-> {out}")
