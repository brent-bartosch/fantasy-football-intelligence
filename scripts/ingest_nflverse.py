#!/usr/bin/env python3
"""Load nflverse weekly player stats into raw.nflverse_player_week.

ADR Precondition P1: this script was in no plist and no crontab and had gone
1209h (50 days) stale while the briefing rendered it [OK]. It is now a step
in scripts/morning_chain.sh.
"""
import argparse

from ffi.db import connect
from ffi.ingest.nflverse import NflversePlayerWeekIngester, parse_seasons

parser = argparse.ArgumentParser()
parser.add_argument("--seasons", default="2019-2025", help="e.g. 2019-2025 or 2024")
args = parser.parse_args()
run_id = NflversePlayerWeekIngester(seasons=parse_seasons(args.seasons)).run(connect())
print(f"OK run_id={run_id}")
