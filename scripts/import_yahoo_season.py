#!/usr/bin/env python3
"""Import a season's draft results into public.draft_picks and weekly player stats
into raw.yahoo_player_week. Idempotent; throttled (R15)."""
import argparse
import json
from ffi.db import connect
from ffi.ids import league_game_code, player_key as make_player_key, player_numeric_id
from ffi.yahoo_client import get_session, get_league, yahoo_call

BATCH = 25


def import_draft(conn, lg, league_key: str):
    picks = [p for p in yahoo_call(lg.draft_results) if "player_id" in p]
    if not picks:
        raise SystemExit(f"No draft results for {league_key} — draft not held yet?")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM draft_picks WHERE league_id=%s", (league_key,)
        )
        if cur.fetchone()[0] > 0:
            print(
                f"draft_picks already present for {league_key}; skipping (idempotent)"
            )
            return
        s = yahoo_call(lg.settings)
        num_teams = s["num_teams"]
        # draft_picks.league_id has an FK to leagues — upsert the league row first
        cur.execute(
            """INSERT INTO leagues (league_id, league_name, season_year, num_teams)
               VALUES (%s,%s,%s,%s) ON CONFLICT (league_id) DO NOTHING""",
            (league_key, s["name"], int(s["season"]), int(num_teams)),
        )
        game_code = league_game_code(league_key)
        for p in picks:
            player_key = make_player_key(game_code, p["player_id"])
            cur.execute(
                """INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                   VALUES (%s, %s, 'TBD', 'TBD') ON CONFLICT (yahoo_player_id) DO NOTHING""",
                (player_key, f"Player {player_key}"),
            )
            cur.execute(
                "SELECT player_id FROM players WHERE yahoo_player_id=%s", (player_key,)
            )
            pid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO draft_picks (league_id, player_id, round_number, pick_number, overall_pick)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    league_key,
                    pid,
                    int(p["round"]),
                    (int(p["pick"]) - 1) % int(num_teams) + 1,
                    int(p["pick"]),
                ),
            )
    conn.commit()
    print(
        f"Imported {len(picks)} draft picks for {league_key} "
        f"(then run fix_placeholder_players.py for any new placeholders)"
    )


def import_weeks(conn, lg, league_key: str, season: int, weeks: list[int]):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT p.yahoo_player_id FROM players p
            JOIN draft_picks dp ON dp.player_id = p.player_id
            WHERE dp.league_id = %s
        """,
            (league_key,),
        )
        ids = [int(player_numeric_id(r[0])) for r in cur.fetchall()]
    if not ids:
        raise SystemExit(f"no drafted players for {league_key} — run --draft first")
    print(f"{len(ids)} players, weeks {weeks}")
    for week in weeks:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM raw.yahoo_player_week WHERE league_key=%s AND week=%s",
                (league_key, week),
            )
            existing = cur.fetchone()[0]
            # lg.player_stats returns one row per requested id (zeros for inactive
            # players), so a complete week has exactly len(ids) rows. Per-batch
            # commits mean a mid-week crash can leave fewer — re-fetch the whole
            # week loudly; ON CONFLICT DO NOTHING makes the re-fetch self-healing.
            if existing == len(ids):
                print(f"  week {week}: already imported (complete), skipping")
                continue
            if existing > 0:
                print(
                    f"  week {week}: INCOMPLETE ({existing}/{len(ids)}) — re-fetching"
                )
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
                            league_key,
                            season,
                            week,
                            str(s["player_id"]),
                            s.get("total_points"),
                            json.dumps(s, default=str),
                        ),
                    )
            conn.commit()
        print(f"  week {week}: done")


def import_outcomes(conn, lg, league_key: str, season: int, force: bool = False):
    """Final standings + weekly team scoreboards + full transaction log (the season time-series).

    Standings and transactions are skipped when already present unless
    force=True (--force-outcomes) — saves API budget on re-runs. Matchups
    already had a per-week skip (idempotent); it's untouched here. NOTE: the
    guard means a mid-season re-run won't pick up NEW transactions without
    --force-outcomes — correct for archived (completed) seasons, and the
    flag handles live in-season re-runs."""
    # Standings: one call. Parse rank/name minimally; keep the full entry as payload.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw.yahoo_standings WHERE league_key=%s",
            (league_key,),
        )
        n = cur.fetchone()[0]
    if n > 0 and not force:
        print(
            f"  standings: {n} teams already present, skipping (--force-outcomes to re-fetch)"
        )
    else:
        standings = yahoo_call(lg.standings)
        with conn.cursor() as cur:
            for i, entry in enumerate(standings):
                cur.execute(
                    """INSERT INTO raw.yahoo_standings (league_key, team_key, season, team_name, final_rank, payload)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (league_key, team_key) DO UPDATE SET payload=EXCLUDED.payload, fetched_at=now()""",
                    (
                        league_key,
                        entry.get("team_key", f"{league_key}.t.{i+1}"),
                        season,
                        entry.get("name"),
                        int(entry["rank"]) if entry.get("rank") else None,
                        json.dumps(entry, default=str),
                    ),
                )
        conn.commit()
        print(f"  standings: {len(standings)} teams")

    # Weekly scoreboards: one call per week; store raw, parse in Phase 2.
    end_week = int(yahoo_call(lg.end_week))
    for week in range(1, end_week + 1):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM raw.yahoo_matchups WHERE league_key=%s AND week=%s",
                (league_key, week),
            )
            if cur.fetchone():
                continue
        payload = yahoo_call(lg.matchups, week=week)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.yahoo_matchups (league_key, season, week, payload) VALUES (%s,%s,%s,%s)",
                (league_key, season, week, json.dumps(payload, default=str)),
            )
        conn.commit()
    print(f"  matchups: weeks 1-{end_week}")

    # Transactions: full log (adds/drops/trades). One-ish call; count=999 requests everything.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw.yahoo_transactions WHERE league_key=%s",
            (league_key,),
        )
        n = cur.fetchone()[0]
    if n > 0 and not force:
        print(
            f"  transactions: {n} already present, skipping (--force-outcomes to re-fetch)"
        )
        return
    txns = yahoo_call(lg.transactions, "add,drop,trade", 999)
    if len(txns) >= 999:
        raise SystemExit(
            f"{league_key}: transaction fetch hit the 999 cap — results would be truncated; raise the cap"
        )
    with conn.cursor() as cur:
        for t in txns:
            cur.execute(
                """INSERT INTO raw.yahoo_transactions (league_key, transaction_key, season, type, ts, payload)
                   VALUES (%s,%s,%s,%s, to_timestamp(%s), %s)
                   ON CONFLICT (league_key, transaction_key) DO NOTHING""",
                (
                    league_key,
                    t["transaction_key"],
                    season,
                    t.get("type"),
                    int(t["timestamp"]) if t.get("timestamp") else None,
                    json.dumps(t, default=str),
                ),
            )
    conn.commit()
    print(f"  transactions: {len(txns)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-key", required=True)
    ap.add_argument("--draft", action="store_true")
    ap.add_argument(
        "--outcomes",
        action="store_true",
        help="standings + weekly scoreboards + transactions",
    )
    ap.add_argument(
        "--force-outcomes",
        action="store_true",
        help="re-fetch standings/transactions even if present (matchups always skip "
        "per-week regardless of this flag — they're immutable once a week is final)",
    )
    ap.add_argument("--weeks", default=None, help="e.g. 1-17 (per-player stats)")
    args = ap.parse_args()

    conn = connect()
    session = get_session()
    lg = get_league(session, args.league_key)
    season = int(yahoo_call(lg.settings)["season"])

    if args.draft:
        import_draft(conn, lg, args.league_key)
    if args.outcomes:
        import_outcomes(conn, lg, args.league_key, season, force=args.force_outcomes)
    if args.weeks:
        lo, hi = (
            args.weeks.split("-") if "-" in args.weeks else (args.weeks, args.weeks)
        )
        import_weeks(
            conn, lg, args.league_key, season, list(range(int(lo), int(hi) + 1))
        )


if __name__ == "__main__":
    main()
