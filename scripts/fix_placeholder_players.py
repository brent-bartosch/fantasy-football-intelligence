#!/usr/bin/env python3
"""Backfill real names/positions/teams for placeholder players created by the legacy import."""
import time
from ffi.db import connect
from ffi.yahoo_client import get_session, get_league

BATCH = 25


def numeric_id(player_key: str) -> str:
    # '461.p.12345' -> '12345'; bare '12345' stays as is
    return player_key.split(".p.")[-1]


def main():
    conn = connect()
    session = get_session()
    with conn.cursor() as cur:
        # Group placeholder players by the league they were drafted in (correct game context)
        cur.execute(
            """
            SELECT DISTINCT l.league_id, p.player_id, p.yahoo_player_id
            FROM players p
            JOIN draft_picks dp ON dp.player_id = p.player_id
            JOIN leagues l ON l.league_id = dp.league_id
            WHERE p.player_name LIKE 'Player %' OR p.position = 'TBD'
            ORDER BY l.league_id
        """
        )
        rows = cur.fetchall()
    print(f"{len(rows)} placeholder player-league rows to resolve")

    by_league: dict[str, list[tuple[int, str]]] = {}
    for league_id, player_id, ykey in rows:
        by_league.setdefault(league_id, []).append((player_id, ykey))

    fixed, failed = 0, []
    for league_key, players in by_league.items():
        lg = get_league(session, league_key)
        for i in range(0, len(players), BATCH):
            chunk = players[i : i + BATCH]
            ids = [int(numeric_id(k)) for _, k in chunk]
            try:
                details = lg.player_details(ids)
            except Exception as exc:  # fail loud per-chunk, keep going, report at end
                failed.append((league_key, ids, str(exc)))
                time.sleep(2)
                continue
            by_id = {str(d["player_id"]): d for d in details}
            with conn.cursor() as cur:
                for player_id, ykey in chunk:
                    d = by_id.get(numeric_id(ykey))
                    if d is None:
                        failed.append((league_key, ykey, "not in response"))
                        continue
                    cur.execute(
                        "UPDATE players SET player_name=%s, position=%s, nfl_team=%s WHERE player_id=%s",
                        (
                            d["name"]["full"],
                            d.get("primary_position", "TBD"),
                            d.get("editorial_team_abbr", "TBD"),
                            player_id,
                        ),
                    )
                    fixed += 1
            conn.commit()
            time.sleep(2)  # throttle (R15)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM players WHERE player_name LIKE 'Player %' OR position='TBD'"
        )
        remaining = cur.fetchone()[0]
    print(
        f"Fixed: {fixed}. Remaining placeholders: {remaining}. Failures: {len(failed)}"
    )
    for f in failed[:20]:
        print("  FAIL:", f)
    if remaining:
        print("Residual placeholders need manual attention — report count to the user.")


if __name__ == "__main__":
    main()
