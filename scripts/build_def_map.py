#!/usr/bin/env python3
"""Populate public.team_def_map from DEF rows in players. Yahoo DEF ids are
numeric (e.g. Chiefs = 100012); names are nicknames. Fail loud on any nickname
not in the static map or if fewer than 24 teams resolve (drafted-DEF coverage
should span most of the league across 16 seasons)."""
from ffi.db import connect
from ffi.ids import normalize_team_abbr

NICKNAME_TO_ABBR = {
    "Cardinals": "ARI",
    "Falcons": "ATL",
    "Ravens": "BAL",
    "Bills": "BUF",
    "Panthers": "CAR",
    "Bears": "CHI",
    "Bengals": "CIN",
    "Browns": "CLE",
    "Cowboys": "DAL",
    "Broncos": "DEN",
    "Lions": "DET",
    "Packers": "GB",
    "Texans": "HOU",
    "Colts": "IND",
    "Jaguars": "JAX",
    "Chiefs": "KC",
    "Rams": "LA",
    "Chargers": "LAC",
    "Raiders": "LV",
    "Dolphins": "MIA",
    "Vikings": "MIN",
    "Patriots": "NE",
    "Saints": "NO",
    "Giants": "NYG",
    "Jets": "NYJ",
    "Eagles": "PHI",
    "Steelers": "PIT",
    "Seahawks": "SEA",
    "49ers": "SF",
    "Buccaneers": "TB",
    "Titans": "TEN",
    "Commanders": "WAS",
    "Redskins": "WAS",
    "Football Team": "WAS",
}

conn = connect()
with conn.cursor() as cur:
    cur.execute(
        """SELECT DISTINCT split_part(yahoo_player_id, '.p.', 2), player_name
           FROM players WHERE position = 'DEF'
             AND split_part(yahoo_player_id, '.p.', 2) ~ '^[0-9]+$'"""
    )
    rows = cur.fetchall()

unknown = [name for _, name in rows if name not in NICKNAME_TO_ABBR]
if unknown:
    raise SystemExit(
        f"unmapped DEF nicknames {sorted(set(unknown))} — extend NICKNAME_TO_ABBR"
    )

with conn.cursor() as cur:
    for def_id, name in rows:
        abbr = normalize_team_abbr(NICKNAME_TO_ABBR[name])
        cur.execute(
            """INSERT INTO public.team_def_map (yahoo_def_id, team_abbr, team_name)
               VALUES (%s,%s,%s)
               ON CONFLICT (yahoo_def_id) DO UPDATE SET team_abbr=EXCLUDED.team_abbr""",
            (def_id, abbr, name),
        )
conn.commit()
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM public.team_def_map")
    n = cur.fetchone()[0]
print(f"team_def_map: {n} defenses mapped")
if n < 24:
    raise SystemExit(
        f"only {n} defenses mapped — expected most of 32; investigate players DEF rows"
    )
