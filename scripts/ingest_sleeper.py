#!/usr/bin/env python3
"""Snapshot Sleeper projections into raw.sleeper_projections. Fail-loud; exits nonzero on any error."""
import argparse
import json
import sys

from ffi.db import connect
from ffi.ingest.base import SANITY_MODES
from ffi.ingest.sleeper import SleeperProjectionsIngester

parser = argparse.ArgumentParser()
parser.add_argument("--season", type=int, required=True)
parser.add_argument(
    "--week", type=int, default=None, help="omit for season-level projections"
)
parser.add_argument(
    "--inspect", action="store_true", help="print first record and exit (no DB write)"
)
parser.add_argument(
    "--sanity-mode",
    choices=SANITY_MODES,
    default=None,
    help=(
        "override the ingester's sanity-gate mode for THIS run "
        "(off|warn|fail); default: the class setting, currently the "
        "'warn' soak that flips to 'fail' on 2026-09-08"
    ),
)
args = parser.parse_args()

ing = SleeperProjectionsIngester(
    season=args.season, week=args.week, sanity_mode=args.sanity_mode
)
if args.inspect:
    payload = ing.fetch()
    print(json.dumps(payload[0] if payload else payload, indent=2))
    sys.exit(0)
conn = connect()
run_id = ing.run(conn)
# Print the status, not a bare "OK": in the warn soak a run that TRIPPED the
# gate still returns a run_id, and an operator reading "OK" would never look
# again. The rho is the soak's measurement (migration 011).
with conn.cursor() as cur:
    cur.execute(
        "SELECT status, sanity_rho, error FROM raw.ingest_runs WHERE run_id=%s",
        (run_id,),
    )
    status, sanity_rho, error = cur.fetchone()
print(f"run_id={run_id} status={status} sanity_rho={sanity_rho}")
if error:
    print(f"gate: {error}")
