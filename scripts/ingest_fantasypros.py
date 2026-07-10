#!/usr/bin/env python3
"""FantasyPros daily sync (<=30 calls, budget-enforced in FpClient).
--probe: exploratory single call, prints top-level keys + first player."""
import argparse
import json

from ffi.db import connect
from ffi.ingest.fantasypros import FpClient, fp_calls_today

SEASON = 2026
# OP = superflex ECR, verified live 2026-07-09: {"type":"draft","scoring":"PPR","week":0}
# returns position_id="OP" with mixed-position players (QB/RB/WR seen) ranked overall.
# NOTE: this API key/tier is public_api_limited=True, capping "players" at 10 records
# per call regardless of the true player pool (count field reports the full pool size,
# e.g. 501 for OP) — see task-10-report.md for detail. Task 11 must account for this cap.
RANKING_POSITIONS = ["OP", "QB", "RB", "WR", "TE", "K", "DST"]


def daily_sync(conn):
    client = FpClient(conn)
    for pos in RANKING_POSITIONS:
        payload = client.get(
            "consensus-rankings",
            {"type": "draft", "scoring": "PPR", "position": pos, "week": 0},
            season=SEASON,
        )
        players = payload.get("players")
        if not players:
            raise SystemExit(
                f"consensus-rankings position={pos} returned no players — payload keys: "
                f"{sorted(payload)[:20]}. Fix params before burning more budget."
            )
        print(f"  rankings {pos}: {len(players)} players")
    print(f"done; calls today: {fp_calls_today(conn)}/30")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--probe",
        nargs=2,
        metavar=("ENDPOINT", "PARAMS_JSON"),
        help='e.g. --probe consensus-rankings \'{"position":"OP","type":"draft","scoring":"PPR","week":0}\'',
    )
    ap.add_argument("--daily", action="store_true")
    args = ap.parse_args()
    conn = connect()
    if args.probe:
        client = FpClient(conn)
        payload = client.get(args.probe[0], json.loads(args.probe[1]), season=SEASON)
        print("top-level keys:", sorted(payload)[:20])
        players = payload.get("players") or []
        print(
            "first player:",
            json.dumps(players[0], indent=1)[:800] if players else "NONE",
        )
    elif args.daily:
        daily_sync(conn)
    else:
        ap.error("choose --probe or --daily")


if __name__ == "__main__":
    main()
