#!/usr/bin/env python3
"""Score a Sleeper snapshot's records under the scoring config into
scoring.projection_points. Default: the latest season-level snapshot."""
import argparse
import json

import psycopg2.extras

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.fd_impute import fit_fd_rates, impute_fd
from ffi.scoring.sleeper_adapter import stat_line_from_sleeper

# FD-imputing positions: rush/rec first downs are only meaningfully modeled
# for skill positions with rush/rec volume. Sleeper's native rush_fd/rec_fd
# are rejected as a scoring input (see sleeper_adapter._IGNORED_EXACT and
# docs/research/2026-07-09-fd-imputation-divergence.md) — imputed FD from
# ffi.scoring.fd_impute (fitted on nflverse 2019-2025 ground truth) is the
# FD source for ALL projection scoring instead.
_FD_IMPUTED_POSITIONS = ("QB", "RB", "WR", "TE")
_FD_FIT_SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]

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
    # sleeper_id -> gsis_id, for player-level FD imputation rates.
    cur.execute(
        "SELECT sleeper_id, gsis_id FROM public.player_id_xwalk WHERE sleeper_id IS NOT NULL"
    )
    sleeper_to_gsis = dict(cur.fetchall())

fd_rates = fit_fd_rates(conn, seasons=_FD_FIT_SEASONS)

horizon = "season" if week is None else f"week:{week}"
out = []
for rec in payload:
    stats = rec.get("stats", {})
    pos = (rec.get("player") or {}).get("position")
    line = stat_line_from_sleeper(rec)
    fd_source = None
    if pos in _FD_IMPUTED_POSITIONS:
        gsis_id = sleeper_to_gsis.get(str(rec["player_id"]))
        imputed = impute_fd(
            fd_rates,
            pos,
            gsis_id,
            carries=float(stats.get("rush_att", 0) or 0),
            receptions=float(stats.get("rec", 0) or 0),
            completions=float(stats.get("pass_cmp", 0) or 0),
        )
        # pass_first_downs stays unset — not a scored stat (see sleeper_adapter).
        line = line.model_copy(
            update={
                "rush_first_downs": imputed["rush_first_downs"],
                "rec_first_downs": imputed["rec_first_downs"],
            }
        )
        fd_source = "imputed"
    comps = score_components(line, cfg)
    points = sum(comps.values())
    comps_out = {k: str(v) for k, v in comps.items()}
    if fd_source is not None:
        comps_out["fd_source"] = fd_source
    out.append(
        (
            "sleeper",
            snapshot_id,
            str(rec["player_id"]),
            horizon,
            cfg.version,
            points,
            json.dumps(comps_out),
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
