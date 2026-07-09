#!/usr/bin/env python3
"""Snapshot Sleeper projections into raw.sleeper_projections. Fail-loud; exits nonzero on any error."""
import argparse
import json
import sys

from ffi.db import connect
from ffi.ingest.sleeper import SleeperProjectionsIngester

parser = argparse.ArgumentParser()
parser.add_argument("--season", type=int, required=True)
parser.add_argument(
    "--week", type=int, default=None, help="omit for season-level projections"
)
parser.add_argument(
    "--inspect", action="store_true", help="print first record and exit (no DB write)"
)
args = parser.parse_args()

ing = SleeperProjectionsIngester(season=args.season, week=args.week)
if args.inspect:
    payload = ing.fetch()
    print(json.dumps(payload[0] if payload else payload, indent=2))
    sys.exit(0)
conn = connect()
run_id = ing.run(conn)
print(f"OK run_id={run_id}")
