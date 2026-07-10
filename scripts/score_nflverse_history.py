#!/usr/bin/env python3
"""Score all raw.nflverse_player_week rows (2019-2025) under config v1 into
scoring.player_week_points (source='nflverse'). Recomputable; idempotent upsert."""
import json

import psycopg2.extras

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.nflverse_adapter import stat_line_from_nflverse

cfg = load_config_v1()
conn = connect()
ensure_config_in_db(conn, cfg)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT * FROM raw.nflverse_player_week")
    rows = cur.fetchall()

out = []
for row in rows:
    comps = score_components(stat_line_from_nflverse(dict(row)), cfg)
    out.append(
        (
            "nflverse",
            row["gsis_id"],
            row["season"],
            row["week"],
            cfg.version,
            sum(comps.values()),
            json.dumps({k: str(v) for k, v in comps.items()}),
        )
    )

with conn.cursor() as cur:
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO scoring.player_week_points
           (source, player_ref, season, week, config_version, points, components)
           VALUES %s
           ON CONFLICT (source, player_ref, season, week, config_version)
           DO UPDATE SET points=EXCLUDED.points, components=EXCLUDED.components, computed_at=now()""",
        out,
        page_size=5000,
    )
conn.commit()
print(f"scored {len(out)} nflverse player-weeks under config v{cfg.version}")
