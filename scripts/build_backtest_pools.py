#!/usr/bin/env python3
"""Build `sim.backtest_pool` for the 2021-25 backtest seasons (Phase 3 /
Task 11; extended to 2021-2022 for the 5-season tournament, 2026-07-21).
Idempotent (each season's rows are fully replaced). Prints per-season
position counts, match rate, and which positions were degraded -- see
`ffi.sim.backtest` module docstring for the fixed decisions this implements
(fpts never trusted, K always degraded, DEF neutralized, fp_id-first name
matching, etc.).

`--seasons` restricts the build to a subset (idempotent, same-machinery). It
is the safe way to add 2021/2022 without rebuilding the frozen 2023-25 gate
pools: those pools' synthetic curves and K rows are copied from the CURRENT
(2026) `qb_hoard_12` pool at build time, so re-running them against a later
2026 pool could shift the D7 gate. Leave the gate pools alone; build only
what you mean to add (`--seasons 2021 2022`)."""
import argparse

from ffi.db import connect
from ffi.scoring.bonus_pricing import estimate_weekly_cv
from ffi.scoring.config import load_config_v1
from ffi.sim.backtest import BACKTEST_SEASONS, build_season_pool, upsert_season_pool
from ffi.sim.pool import build_pool

_CV_SEASONS = list(range(2019, 2026))  # all available -- module docstring point 11


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--seasons",
        nargs="+",
        type=int,
        default=list(BACKTEST_SEASONS),
        help="subset of backtest seasons to (re)build (default: all BACKTEST_SEASONS)",
    )
    args = ap.parse_args()
    seasons = args.seasons

    conn = connect()
    cfg = load_config_v1()
    cv = estimate_weekly_cv(conn, seasons=_CV_SEASONS)
    current_pool = build_pool(conn, "qb_hoard_12")
    print(f"current (2026) qb_hoard_12 pool: {len(current_pool)} players")

    for season in seasons:
        rows, report = build_season_pool(conn, season, current_pool, cfg, cv)
        upsert_season_pool(conn, season, rows)
        print(f"\n=== season {season} ===")
        print(f"  real projections: {report['real_projection_positions']}")
        print(f"  degraded positions: {report['degraded_positions']}")
        print(
            f"  name match: {report['match_resolved']}/{report['match_total']} "
            f"({report['match_resolved'] / max(report['match_total'], 1):.1%}), "
            f"{report['unmatched_count']} unmatched (excluded from pool)"
        )
        print(f"  counts: {report['counts']}")

    print("\nbuild_backtest_pools.py: done")


if __name__ == "__main__":
    main()
