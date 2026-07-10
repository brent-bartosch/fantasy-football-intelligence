#!/usr/bin/env python3
"""Fetch 2025 weekly stats for DEF/K ids missing from raw.yahoo_player_week.
DEF ids come from team_def_map (all 32); K ids from Yahoo's 2025 player pool
via league taken/available listing. Throttled via yahoo_call; resumable (skips
already-present rows via ON CONFLICT).

API-shape deviations from the original brief, verified live before trusting
them (Task 12 Step 2 instructions required this):

1. `lg.taken_players()` (no position arg) paginates 25-at-a-time through
   ALL taken players across every roster position (~190 in this 12-team
   league -> ~8 back-to-back HTTP requests inside a single Python call,
   with NO spacing between the internal page fetches -- yahoo_call only
   throttles the outer call, not yfa's internal pagination loop). That
   defeats the >=2s spacing safety rule (R15) and risks a 999 lockout for
   no benefit, since we only need the K subset. Fixed by calling the
   library's own (private, but used internally by free_agents()) filtered
   fetch: `lg._fetch_players('T', position='K')` -- Yahoo filters
   server-side, so it's 1 page (verified: 12 taken K's, 1 HTTP call).

2. `lg.free_agents('K')` returned ZERO records when probed live (verified
   2026-07-09, off-season / post-2025-season). Root cause: Yahoo's 'FA'
   status means "immediately pickupable," but every currently-unrostered
   kicker in this league sits on 'W' (waivers) instead in this league
   state. Probing status='A' (all available = FA+W) returned 23 records
   with the expected shape. Per the brief ("adjust the filter to reality;
   zero K ids extracted = fail loud"), this is the adjustment -- not a
   silent guess: both statuses were probed live first.

Verified record shape (both calls), e.g.:
  {'player_id': 28188, 'name': 'Chris Boswell', 'editorial_team_abbr': 'Pit',
   'position_type': 'K', 'eligible_positions': ['K'], 'percent_owned': 75,
   'status': ''}
`position_type == 'K'` is a reliable filter (matches the brief's guess).

3. `public.v_player_yahoo_ids` (which the Step 3 report joins through to get
   player names) is a view over `players`, which only knows about players
   ever drafted/rostered in a Yahoo league we've previously scraped. 15 of
   the 35 fetched 2025 K ids (mostly recent/rookie kickers, e.g. Will
   Reichard, Cade York, Andy Borregales) had never appeared in `players`
   under any game key — they would silently vanish from the report's
   streaming pool (35 -> 20/week) even though raw.yahoo_player_week and
   scoring.player_week_points have their rows. Fixed below by inserting a
   minimal `players` row (game key 461 = the 2025 season) for any fetched
   K id not already present, so the report's pool reflects the true ~35.
"""
import json

from ffi.db import connect
from ffi.ids import normalize_team_abbr
from ffi.yahoo_client import get_league, get_session, yahoo_call

LEAGUE_KEY = "461.l.326814"
SEASON = 2025
BATCH = 25

conn = connect()
lg = get_league(get_session(), LEAGUE_KEY)

with conn.cursor() as cur:
    cur.execute("SELECT yahoo_def_id FROM public.team_def_map ORDER BY 1")
    def_ids = [int(r[0]) for r in cur.fetchall()]
if len(def_ids) < 28:
    raise SystemExit(
        f"team_def_map has only {len(def_ids)} defenses — run scripts/build_def_map.py first"
    )

# K pool: taken K's + all currently-available K's (FA + waivers), both
# filtered server-side to position='K' to avoid yfa's unthrottled
# multi-page pagination on the unfiltered (all-position) taken_players() call.
taken_k = yahoo_call(lg._fetch_players, "T", position="K")
available_k = yahoo_call(lg._fetch_players, "A", position="K")
k_records = taken_k + available_k
k_ids = sorted(
    {int(p["player_id"]) for p in k_records if p.get("position_type") == "K"}
)
if not k_ids:
    raise SystemExit(
        f"zero K ids extracted from taken(n={len(taken_k)})/available(n={len(available_k)}) — "
        "Yahoo record shape changed; inspect a sample record and fix the filter before retrying"
    )
print(
    f"{len(def_ids)} DEF ids, {len(k_ids)} K ids (taken={len(taken_k)}, available={len(available_k)})"
)

# Register any K id never before seen in `players` so it survives the
# report's v_player_yahoo_ids join (see deviation 3 above).
with conn.cursor() as cur:
    cur.execute(
        "SELECT split_part(yahoo_player_id,'.p.',2) FROM players WHERE position='K'"
    )
    known_k = {r[0] for r in cur.fetchall()}
by_id = {int(p["player_id"]): p for p in k_records if p.get("position_type") == "K"}
new_players = 0
with conn.cursor() as cur:
    for pid in k_ids:
        if str(pid) in known_k:
            continue
        rec = by_id[pid]
        team = rec.get("editorial_team_abbr")
        try:
            team_abbr = normalize_team_abbr(team) if team else None
        except Exception:
            team_abbr = None  # unresolved abbreviation: leave nfl_team NULL, not fatal
        cur.execute(
            """INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
               VALUES (%s,%s,'K',%s) ON CONFLICT (yahoo_player_id) DO NOTHING""",
            (f"461.p.{pid}", rec["name"], team_abbr),
        )
        new_players += 1
conn.commit()
if new_players:
    print(f"  registered {new_players} previously-unseen K players in `players` table")

for week in range(1, 18):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM raw.yahoo_player_week w
               WHERE w.league_key=%s AND w.week=%s
                 AND w.yahoo_player_id = ANY(%s)""",
            (LEAGUE_KEY, week, [str(i) for i in def_ids + k_ids]),
        )
        have = cur.fetchone()[0]
    want = len(def_ids) + len(k_ids)
    if have >= want:
        print(f"  week {week}: complete ({have}/{want}), skipping")
        continue
    ids = def_ids + k_ids
    for i in range(0, len(ids), BATCH):
        stats = yahoo_call(lg.player_stats, ids[i : i + BATCH], "week", week=week)
        with conn.cursor() as cur:
            for s in stats:
                cur.execute(
                    """INSERT INTO raw.yahoo_player_week
                       (league_key, season, week, yahoo_player_id, total_points, stats)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (league_key, week, yahoo_player_id) DO NOTHING""",
                    (
                        LEAGUE_KEY,
                        SEASON,
                        week,
                        str(s["player_id"]),
                        s.get("total_points"),
                        json.dumps(s, default=str),
                    ),
                )
        conn.commit()
    print(f"  week {week}: done")
