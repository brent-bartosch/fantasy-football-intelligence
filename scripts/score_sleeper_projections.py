#!/usr/bin/env python3
"""Score a Sleeper snapshot's records under the scoring config into
scoring.projection_points. Default: the latest season-level snapshot."""
import argparse
import json

import psycopg2.extras

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.sleeper_adapter import stat_line_from_sleeper

ap = argparse.ArgumentParser()
ap.add_argument(
    "--snapshot-id", type=int, default=None, help="default: latest week-NULL snapshot"
)
args = ap.parse_args()

cfg = load_config_v1()
conn = connect()
ensure_config_in_db(conn, cfg)
with conn.cursor() as cur:
    if args.snapshot_id is None:
        cur.execute(
            "SELECT snapshot_id, season, week FROM raw.sleeper_projections "
            "WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1"
        )
    else:
        cur.execute(
            "SELECT snapshot_id, season, week FROM raw.sleeper_projections WHERE snapshot_id=%s",
            (args.snapshot_id,),
        )
    row = cur.fetchone()
    if row is None:
        raise SystemExit(
            "no matching sleeper snapshot — run scripts/ingest_sleeper.py first"
        )
    snapshot_id, season, week = row
    cur.execute(
        "SELECT payload FROM raw.sleeper_projections WHERE snapshot_id=%s",
        (snapshot_id,),
    )
    payload = cur.fetchone()[0]

horizon = "season" if week is None else f"week:{week}"
out = []
for rec in payload:
    comps = score_components(stat_line_from_sleeper(rec), cfg)
    out.append(
        (
            "sleeper",
            snapshot_id,
            str(rec["player_id"]),
            horizon,
            cfg.version,
            sum(comps.values()),
            json.dumps({k: str(v) for k, v in comps.items()}),
        )
    )
with conn.cursor() as cur:
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO scoring.projection_points
           (source, snapshot_id, player_ref, horizon, config_version, points, components)
           VALUES %s
           ON CONFLICT (source, snapshot_id, player_ref, config_version)
           DO UPDATE SET points=EXCLUDED.points, components=EXCLUDED.components, computed_at=now()""",
        out,
        page_size=2000,
    )
conn.commit()
print(
    f"scored {len(out)} records from snapshot {snapshot_id} ({season} {horizon}) under v{cfg.version}"
)
