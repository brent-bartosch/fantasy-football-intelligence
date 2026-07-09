#!/usr/bin/env python3
"""Load nflverse weekly player stats into raw.nflverse_player_week."""
import argparse
from ffi.db import connect
from ffi.ingest.nflverse import NflversePlayerWeekIngester

parser = argparse.ArgumentParser()
parser.add_argument("--seasons", default="2019-2025", help="e.g. 2019-2025 or 2024")
args = parser.parse_args()
if "-" in args.seasons:
    lo, hi = args.seasons.split("-")
    seasons = list(range(int(lo), int(hi) + 1))
else:
    seasons = [int(args.seasons)]
run_id = NflversePlayerWeekIngester(seasons=seasons).run(connect())
print(f"OK run_id={run_id}")
