#!/usr/bin/env python3
"""Flatten all raw.yahoo_matchups payloads into public.matchup_results.

Validation per league-week:
  - regular-season weeks (is_playoffs False) must have rows == num_teams,
    else SystemExit (a missing team in a regular-season week is corruption).
  - playoff weeks (is_playoffs True) are allowed rows < num_teams: live-data
    fact-check (see docs below) showed Yahoo's playoff/consolation bracket
    does NOT give every team a matchup every week once brackets narrow (some
    teams have no scoreboard entry at all in a given playoff week — this is
    real Yahoo behavior, not a parse error). We still fail loud on rows == 0
    (parse_matchup_payload already refuses that) and on a payload whose
    matchups disagree on is_playoffs (would indicate real shape drift).

Discovered while fact-checking Step 3 of the task-13 brief:
  - 2010-2020 (12-team, 16-week seasons): weeks 14 and 16 have 8 rows
    (4 matchups) instead of 12 — playoff bracket byes.
  - 2021-2025 (12-team, 17-week seasons): weeks 15-17 have 6 rows
    (2025) or 8 rows — same cause, narrowing bracket.
  - Every regular-season week checked across all 16 seasons has exactly
    num_teams rows; total actual rows = 2,994 (not the naive 261*12=3,132
    estimate in the brief, which assumed uniform 12-per-week — the ~ in
    "~3,132" already flagged this as an estimate).
"""
from ffi.db import connect
from ffi.history.matchups import parse_matchup_payload
from ffi.ids import team_slot

conn = connect()
with conn.cursor() as cur:
    cur.execute(
        """SELECT m.league_key, m.season, m.week, m.payload, s.num_teams
           FROM raw.yahoo_matchups m
           JOIN raw.yahoo_league_settings s ON s.league_key = m.league_key
           ORDER BY m.season, m.week"""
    )
    payloads = cur.fetchall()

total = 0
partial_playoff_weeks = 0
with conn.cursor() as cur:
    for league_key, season, week, payload, num_teams in payloads:
        rows = parse_matchup_payload(payload)

        playoff_flags = {r["is_playoffs"] for r in rows}
        if len(playoff_flags) != 1:
            raise SystemExit(
                f"{league_key} wk{week}: matchups disagree on is_playoffs "
                f"within one payload ({playoff_flags}) — real shape drift, refusing to guess"
            )
        is_playoffs = playoff_flags.pop()

        if not is_playoffs and len(rows) != num_teams:
            raise SystemExit(
                f"{league_key} wk{week}: parsed {len(rows)} team-rows, expected {num_teams} "
                f"(regular-season week — this is not the known playoff-bracket-bye pattern)"
            )
        if is_playoffs and len(rows) > num_teams:
            raise SystemExit(
                f"{league_key} wk{week}: parsed {len(rows)} team-rows, more than "
                f"num_teams={num_teams} — refusing to guess"
            )
        if is_playoffs and len(rows) < num_teams:
            partial_playoff_weeks += 1
            print(
                f"  {league_key} wk{week}: playoff week, {len(rows)}/{num_teams} team-rows "
                f"(bracket bye — expected, not every team has a scoreboard entry once brackets narrow)"
            )

        for r in rows:
            cur.execute(
                """INSERT INTO public.matchup_results
                   (league_key, season, week, team_key, slot, points, proj_points,
                    opp_team_key, opp_points, is_playoffs)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (league_key, week, team_key) DO UPDATE
                     SET points=EXCLUDED.points, opp_points=EXCLUDED.opp_points,
                         is_playoffs=EXCLUDED.is_playoffs""",
                (
                    league_key,
                    season,
                    week,
                    r["team_key"],
                    team_slot(r["team_key"]),
                    r["points"],
                    r["proj_points"],
                    r["opp_team_key"],
                    r["opp_points"],
                    r["is_playoffs"],
                ),
            )
        total += len(rows)
conn.commit()
print(
    f"matchup_results: {total} team-week rows from {len(payloads)} scoreboards "
    f"({partial_playoff_weeks} playoff weeks had bracket-bye teams)"
)
