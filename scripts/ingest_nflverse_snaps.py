#!/usr/bin/env python3
"""Load nflverse snap counts into raw.nflverse_snap_counts.

Season window is shared with scripts/ingest_nflverse.py via
ffi.ingest.nflverse.parse_seasons so the two feeds can never drift apart.
"""

import argparse

from ffi.db import connect
from ffi.ingest.nflverse import parse_seasons
from ffi.ingest.nflverse_snaps import NflverseSnapCountsIngester

parser = argparse.ArgumentParser()
parser.add_argument("--seasons", default="2019-2025", help="e.g. 2019-2025 or 2025")
args = parser.parse_args()
run_id = NflverseSnapCountsIngester(seasons=parse_seasons(args.seasons)).run(connect())
print(f"OK run_id={run_id}")
