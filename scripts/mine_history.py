#!/usr/bin/env python3
"""Render the historical mining report (the user-facing Phase 2 deliverable)."""
import datetime
import pathlib

from collections import defaultdict

from ffi.db import connect
from ffi.history.mining import (
    all_play,
    champion_value_split,
    draft_position_outcomes,
    franchise_slot_outcomes,
    position_round_tendencies,
    qb_timing_by_slot,
    trade_stats,
    transaction_timing,
)

conn = connect()
today = datetime.date.today().isoformat()


def _validate_draft_position_permutation(conn) -> None:
    """Live guard: each season's round-1 pick_numbers must be a permutation of
    1..12 (this league disallows pick trades, so they should always land as a
    clean snake-draft assignment). Fail loud and name offending leagues/seasons
    otherwise — do not silently tolerate a broken draft-order signal."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT dp.league_id, s.season, dp.pick_number
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.team_id
            JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
            WHERE dp.round_number = 1
            """
        )
        rows = cur.fetchall()
    by_league: dict = defaultdict(list)
    seasons: dict = {}
    for league_id, season, pick_number in rows:
        by_league[league_id].append(pick_number)
        seasons[league_id] = season
    bad = {
        f"{league_id} (season {seasons[league_id]})": sorted(picks)
        for league_id, picks in by_league.items()
        if sorted(picks) != list(range(1, 13))
    }
    if bad:
        raise ValueError(
            f"draft-position validation FAILED: {len(bad)} league-season(s) do "
            f"not have a clean 1..12 round-1 pick_number permutation (possible "
            f"traded picks despite the league's no-trade-picks rule): {bad}"
        )
    total = sum(len(v) for v in by_league.values())
    print(
        f"-> draft-position validation PASS: {len(by_league)} leagues, "
        f"{total} round-1 picks, all clean 1..12 permutations"
    )


_validate_draft_position_permutation(conn)
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
    "\n## 1. Franchise slot -> outcome (persistent manager-seat quality; 16 seasons)",
    "`teams.slot` is the stable Yahoo franchise/team seat, not the draft position — "
    "snake-draft order varies every season (see section 1b for that). A franchise "
    "slot can change hands between managers over the league's history (see "
    "`manager_slot_annotations`, e.g. slot 12 = Brent from ~2022-present); this table "
    "measures how strong a *seat* has been across whoever has held it, not any "
    "draft-order advantage.",
    "| slot | seasons | avg finish | titles | avg PF |",
    "|---|---|---|---|---|",
]
for r in franchise_slot_outcomes(conn):
    L.append(
        f"| {r['slot']} | {r['seasons']} | {float(r['avg_finish']):.2f} | {r['titles']} | {float(r['avg_pf'] or 0):.0f} |"
    )

dpo = draft_position_outcomes(conn)
L += [
    "\n## 1b. Draft position -> outcome (snake order, 16 seasons)",
    "TRUE draft position: each team-season's round-1 `pick_number` "
    "(`draft_picks WHERE round_number = 1`) — the actual snake-draft slot a team "
    "drafted from that year, independent of its stable franchise seat. Validated: "
    "192 team-seasons, exactly one round-1 pick each, live permutation check passed "
    "(see script run log above).",
    "| position | seasons | avg finish | titles | avg PF |",
    "|---|---|---|---|---|",
]
for r in dpo:
    L.append(
        f"| {r['position']} | {r['seasons']} | {float(r['avg_finish']):.2f} | {r['titles']} | {float(r['avg_pf'] or 0):.0f} |"
    )

_first_half = [r for r in dpo if r["position"] <= 6]
_second_half = [r for r in dpo if r["position"] > 6]
_avg_first = sum(r["avg_finish"] for r in _first_half) / len(_first_half)
_avg_second = sum(r["avg_finish"] for r in _second_half) / len(_second_half)
_direction = (
    "earlier draft positions finish better on average"
    if _avg_first < _avg_second
    else "later draft positions finish better on average"
    if _avg_second < _avg_first
    else "draft position shows no directional finish advantage either way"
)
L += [
    f"\n**Cross-check (franchise slot vs true draft position):** positions 1-6 "
    f"average a {_avg_first:.2f} finish vs {_avg_second:.2f} for positions 7-12 — "
    f"{_direction}. This is the honest draft-order signal; the much larger spread "
    f"seen in section 1 (franchise slot 8 at 4.88 vs slot 1 at 8.12) is manager-seat "
    f"quality accumulated over 16 years, not a draft-position effect — those two "
    f"tables should not be conflated.",
]

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
