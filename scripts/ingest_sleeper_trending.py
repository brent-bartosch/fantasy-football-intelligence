#!/usr/bin/env python3
"""ADR Precondition P2: archive Sleeper trending add+drop into
raw.sleeper_trending. Fail-loud; exits nonzero on any error so launchd
surfaces a missed day — the one failure this system cannot recover from."""
import argparse
import json
import sys

from ffi.db import connect
from ffi.ingest.sleeper_trending import SleeperTrendingIngester

parser = argparse.ArgumentParser()
parser.add_argument("--lookback-hours", type=int, default=24)
parser.add_argument("--limit", type=int, default=200)
parser.add_argument(
    "--inspect",
    action="store_true",
    help="print the payload head and exit (no DB write)",
)
args = parser.parse_args()

ing = SleeperTrendingIngester(lookback_hours=args.lookback_hours, limit=args.limit)
if args.inspect:
    payload = ing.fetch()
    print(json.dumps({k: v[:3] for k, v in payload.items()}, indent=2))
    sys.exit(0)
run_id = ing.run(connect())
print(f"OK run_id={run_id}")
