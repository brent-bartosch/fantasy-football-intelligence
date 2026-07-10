#!/usr/bin/env python3
"""Score EVERY raw.yahoo_player_week row with the engine and compare to
Yahoo's official total exactly. Persists matches into scoring.player_week_points
(source='yahoo_engine'). Exit code = number of mismatched rows."""
from decimal import Decimal
import json

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo

cfg = load_config_v1()
conn = connect()
ensure_config_in_db(conn, cfg)

with conn.cursor() as cur:
    cur.execute(
        """SELECT league_key, season, week, yahoo_player_id, total_points::text, stats
           FROM raw.yahoo_player_week ORDER BY week, yahoo_player_id"""
    )
    rows = cur.fetchall()

mismatches = []
with conn.cursor() as cur:
    for lk, season, week, pid, tp, stats in rows:
        comps = score_components(stat_line_from_yahoo(stats), cfg)
        got = sum(comps.values(), Decimal("0"))
        if tp is None or got != Decimal(tp):
            mismatches.append((stats.get("name"), week, pid, str(got), tp))
            continue
        cur.execute(
            """INSERT INTO scoring.player_week_points
               (source, player_ref, season, week, config_version, points, components)
               VALUES ('yahoo_engine', %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source, player_ref, season, week, config_version)
               DO UPDATE SET points=EXCLUDED.points, components=EXCLUDED.components,
                             computed_at=now()""",
            (
                pid,
                season,
                week,
                cfg.version,
                got,
                json.dumps({k: str(v) for k, v in comps.items()}),
            ),
        )
conn.commit()
print(f"{len(rows)} rows scored; {len(mismatches)} mismatches")
for m in mismatches[:40]:
    print("  MISMATCH:", m)
raise SystemExit(len(mismatches))
