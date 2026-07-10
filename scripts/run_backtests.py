#!/usr/bin/env python3
"""ADR D7 regression gate (Phase 3 / Task 11): run the 12 reference cells
(4 `REF_STRATEGIES` x 3 `BACKTEST_SEASONS`, 100 seeded drafts each) against
the archived+actuals backtest pools, persist one `sim.batches`/
`batch_results` row per cell for audit, and either:

  --reference   store the composite/band as the new active
                `sim.backtest_reference` row (deactivating any prior one).
  --gate        recompute fresh and compare against the active reference;
                exits nonzero (SystemExit) if composite < reference.composite
                - reference.band, or if no active reference row exists.

Requires `scripts/build_backtest_pools.py` to have populated
`sim.backtest_pool` for all three seasons first.
"""
import argparse
import dataclasses
import json
import pathlib
import subprocess

from ffi.db import connect
from ffi.scoring.config import load_config_v1
from ffi.sim.backtest import (
    BACKTEST_SEASONS,
    REF_STRATEGIES,
    cell_base_seed,
    composite_and_band,
    evaluate_gate,
    run_all_cells,
    season_data_vintage,
)
from ffi.sim.opponent import CAND_WINDOW, ROSTER_DAMP, TAU
from ffi.sim.priors import build_slot_priors

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=str(REPO_ROOT)
        ).strip()
    except Exception:
        return None


def _persist_batches(conn, cfg, results: list, opponent_params: dict) -> None:
    vintage_by_season = {
        season: season_data_vintage(conn, season) for season in BACKTEST_SEASONS
    }
    with conn.cursor() as cur:
        for r in results:
            strat = REF_STRATEGIES[r["strategy_idx"]]
            cur.execute(
                """INSERT INTO sim.batches
                   (kind, git_sha, config_version, scenario, season, strategy,
                    opponent_params, n_drafts, seasons_per_draft, base_seed,
                    data_vintage, finished_at)
                   VALUES ('backtest',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   RETURNING batch_id""",
                (
                    git_sha(),
                    cfg.version,
                    f"backtest_strategy_{r['strategy_idx']}",
                    r["season"],
                    json.dumps(dataclasses.asdict(strat)),
                    json.dumps(opponent_params),
                    r["n_drafts"],
                    1,
                    cell_base_seed(r["strategy_idx"], r["season"]),
                    json.dumps(vintage_by_season[r["season"]]),
                ),
            )
            batch_id = cur.fetchone()[0]
            for metric in ("all_play_pct", "all_play_se", "qb1_round_mean"):
                val = r[metric]
                if val is not None:
                    cur.execute(
                        "INSERT INTO sim.batch_results (batch_id, metric, value) VALUES (%s,%s,%s)",
                        (batch_id, metric, val),
                    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--reference", action="store_true", help="store a new active reference"
    )
    group.add_argument(
        "--gate", action="store_true", help="compare against the active reference"
    )
    args = ap.parse_args()

    conn = connect()
    cfg = load_config_v1()

    print("running 12 reference cells (4 strategies x 3 seasons x 100 drafts)...")
    results = run_all_cells(conn)
    cell_means = [r["all_play_pct"] for r in results]
    composite, band = composite_and_band(cell_means)

    for r in results:
        print(
            f"  strategy={r['strategy_idx']} season={r['season']} "
            f"all_play_pct={r['all_play_pct']:.4f} se={r['all_play_se']:.4f} "
            f"qb1_round_mean={r['qb1_round_mean']}"
        )
    print(f"composite={composite:.4f} band={band:.4f}")

    priors_params = build_slot_priors(conn).params
    opponent_params = {
        "tau": TAU,
        "cand_window": CAND_WINDOW,
        "roster_damp": ROSTER_DAMP,
        "priors": priors_params,
    }
    _persist_batches(conn, cfg, results, opponent_params)

    if args.reference:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sim.backtest_reference SET is_active=false WHERE is_active=true"
            )
            cur.execute(
                """INSERT INTO sim.backtest_reference
                   (git_sha, description, composite, band, detail, is_active)
                   VALUES (%s,%s,%s,%s,%s,true)""",
                (
                    git_sha(),
                    "Task 11 ADR D7 reference: 4 strategies x 3 seasons (2023-25) x 100 drafts",
                    composite,
                    band,
                    json.dumps(results),
                ),
            )
        conn.commit()
        print(f"stored new active reference: composite={composite:.4f} band={band:.4f}")
    else:  # --gate
        with conn.cursor() as cur:
            cur.execute(
                "SELECT composite, band FROM sim.backtest_reference "
                "WHERE is_active=true ORDER BY ref_id DESC LIMIT 1"
            )
            row = cur.fetchone()
        reference = (float(row[0]), float(row[1])) if row is not None else None
        evaluate_gate(composite, reference)
        print(
            f"ADR D7 gate PASSED: composite {composite:.4f} >= threshold "
            f"{reference[0] - reference[1]:.4f}"
        )


if __name__ == "__main__":
    main()
