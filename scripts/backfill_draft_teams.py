#!/usr/bin/env python3
"""1) Populate teams (slot, name, final_rank, points_for, championship flags)
from raw.yahoo_standings payloads — zero API calls.
2) Assign draft_picks.team_id by re-fetching draft_results per season (Phase 1
discarded team_key) — one throttled call per league (16 total). Resumable:
seasons whose picks all have team_id are skipped."""
import json

from ffi.db import connect
from ffi.ids import team_slot
from ffi.yahoo_client import get_league, get_session, yahoo_call

conn = connect()

# --- teams from standings (no API) ---
with conn.cursor() as cur:
    cur.execute(
        """SELECT s.league_key, s.season, s.team_key, s.payload
           FROM raw.yahoo_standings s ORDER BY s.season"""
    )
    standings = cur.fetchall()
with conn.cursor() as cur:
    for league_key, season, team_key, payload in standings:
        rank = int(payload["rank"]) if payload.get("rank") else None
        seed = payload.get("playoff_seed")
        cur.execute(
            """INSERT INTO teams (league_id, team_key, slot, team_name, final_rank,
                                  total_points_scored, playoff_seed, made_playoffs, won_championship)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (team_key) DO UPDATE
                 SET final_rank=EXCLUDED.final_rank, team_name=EXCLUDED.team_name,
                     total_points_scored=EXCLUDED.total_points_scored""",
            (
                league_key,
                team_key,
                team_slot(team_key),
                payload.get("name"),
                rank,
                float(payload["points_for"]) if payload.get("points_for") else None,
                int(seed) if seed else None,
                seed is not None,
                rank == 1,
            ),
        )
conn.commit()
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM teams WHERE slot IS NOT NULL")
    print(f"teams with slots: {cur.fetchone()[0]} (expect 192 = 16 seasons x 12)")

# --- draft_picks.team_id via draft_results re-fetch (16 calls) ---
session = get_session()
with conn.cursor() as cur:
    cur.execute("SELECT league_key FROM raw.yahoo_league_settings ORDER BY season")
    league_keys = [r[0] for r in cur.fetchall()]

for lk in league_keys:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE team_id IS NULL), count(*) FROM draft_picks WHERE league_id=%s",
            (lk,),
        )
        nulls, total = cur.fetchone()
    if total == 0:
        print(f"{lk}: no picks in DB — investigate (all 16 seasons were imported)")
        continue
    if nulls == 0:
        print(f"{lk}: team_id complete, skipping")
        continue
    lg = get_league(session, lk)
    picks = [p for p in yahoo_call(lg.draft_results) if "player_id" in p]
    if len(picks) != total:
        raise SystemExit(
            f"{lk}: API returned {len(picks)} picks but DB has {total} — refusing to guess"
        )
    with conn.cursor() as cur:
        for p in picks:
            if "team_key" not in p:
                raise SystemExit(
                    f"{lk}: draft result lacks team_key: {json.dumps(p)[:200]}"
                )
            cur.execute(
                """UPDATE draft_picks dp SET team_id = t.team_id
                   FROM teams t
                   WHERE dp.league_id=%s AND dp.overall_pick=%s AND t.team_key=%s""",
                (lk, int(p["pick"]), p["team_key"]),
            )
    conn.commit()
    print(f"{lk}: assigned team_id for {len(picks)} picks")

with conn.cursor() as cur:
    cur.execute(
        """SELECT count(*) FROM draft_picks dp
           JOIN raw.yahoo_league_settings s ON s.league_key=dp.league_id
           WHERE dp.team_id IS NULL"""
    )
    remaining = cur.fetchone()[0]
print(f"NAJEE picks still missing team_id: {remaining}")
raise SystemExit(1 if remaining else 0)
