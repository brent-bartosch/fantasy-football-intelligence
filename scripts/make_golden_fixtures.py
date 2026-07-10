#!/usr/bin/env python3
"""Select ~40 golden fixtures from raw.yahoo_player_week (2025) covering every
edge class, and write tests/fixtures/golden_2025.json. Deterministic: ordered
picks per class, no randomness."""
import json
import pathlib

from ffi.db import connect

OUT = pathlib.Path("tests/fixtures/golden_2025.json")

# (label, where-clause, order, limit) against 2025 offense/K/DEF rows.
CLASSES = [
    ("rec_200_plus", "(stats->>'Rec Yds')::float >= 200", "total_points DESC", 2),
    ("rush_200_plus", "(stats->>'Rush Yds')::float >= 200", "total_points DESC", 2),
    (
        "rec_150_band",
        "(stats->>'Rec Yds')::float BETWEEN 150 AND 199",
        "total_points DESC",
        3,
    ),
    (
        "rush_150_band",
        "(stats->>'Rush Yds')::float BETWEEN 150 AND 199",
        "total_points DESC",
        3,
    ),
    (
        "rec_100_band",
        "(stats->>'Rec Yds')::float BETWEEN 100 AND 149",
        "total_points DESC",
        2,
    ),
    (
        "pass_300_band",
        "(stats->>'Pass Yds')::float BETWEEN 300 AND 399",
        "total_points DESC",
        2,
    ),
    ("pass_400_plus", "(stats->>'Pass Yds')::float >= 400", "total_points DESC", 2),
    ("pass_500_plus", "(stats->>'Pass Yds')::float >= 500", "total_points DESC", 2),
    ("pick_six", "(stats->>'Pick Six')::float > 0", "total_points ASC", 3),
    ("negative_total", "total_points < 0", "total_points ASC", 3),
    (
        "return_yards",
        "(stats->>'Ret Yds')::float > 0",
        "(stats->>'Ret Yds')::float DESC",
        3,
    ),
    ("two_point", "(stats->>'2-PT')::float > 0", "total_points DESC", 2),
    ("fum_ret_td", "(stats->>'Fum Ret TD')::float > 0", "total_points DESC", 1),
    ("zero_line", "total_points = 0", "yahoo_player_id", 2),
    (
        "kicker_50_plus",
        "stats->>'position_type'='K' AND (stats->>'FG 50+')::float > 0",
        "total_points DESC",
        2,
    ),
    (
        "kicker_misses",
        "stats->>'position_type'='K' AND ((stats->>'FGM 20-29')::float > 0 "
        "OR (stats->>'FGM 30-39')::float > 0 OR (stats->>'PAT Miss')::float > 0)",
        "total_points ASC",
        2,
    ),
    (
        "def_shutout_or_low",
        "stats->>'position_type'='DT' AND (stats->>'Pts Allow')::float <= 6",
        "total_points DESC",
        2,
    ),
    (
        "def_high_allowed",
        "stats->>'position_type'='DT' AND (stats->>'Pts Allow')::float >= 35",
        "total_points ASC",
        2,
    ),
    (
        "def_stat_stack",
        "stats->>'position_type'='DT' AND (stats->>'TFL')::float >= 5",
        "total_points DESC",
        2,
    ),
]

conn = connect()
fixtures, seen = [], set()
for label, where, order, limit in CLASSES:
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT league_key, week, yahoo_player_id, stats->>'name',
                       total_points::text, stats
                FROM raw.yahoo_player_week
                WHERE season = 2025 AND {where}
                ORDER BY {order}, yahoo_player_id, week LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    if not rows:
        print(
            f"  NOTE: class {label!r} has no 2025 examples (acceptable for 500+ pass)"
        )
    for lk, wk, pid, name, tp, stats in rows:
        key = (lk, wk, pid)
        if key in seen:
            continue
        seen.add(key)
        fixtures.append(
            {
                "class": label,
                "league_key": lk,
                "week": wk,
                "yahoo_player_id": pid,
                "name": name,
                "total_points": tp,
                "stats": stats,
            }
        )

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(fixtures, indent=1, sort_keys=True))
print(f"{len(fixtures)} golden fixtures -> {OUT}")
if len(fixtures) < 35:
    raise SystemExit(
        f"only {len(fixtures)} fixtures — expected ~40; check class queries"
    )
