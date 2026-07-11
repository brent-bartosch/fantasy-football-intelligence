#!/usr/bin/env python3
"""Opponent QB-timing calibration CLI (Phase 4 Task 2).

`--measure` runs the uniform-sample measurement harness (`ffi.sim.calibrate`)
against the live DB's current pool/priors and prints a markdown gap report
against `qb_timing_by_slot`'s historical timing. This is a measurement-only
pass -- no model change, no persistence; Task 4 wires the fitted result into
the sim farm.
"""
import argparse

from ffi.db import connect
from ffi.sim.calibrate import historical_qb_timing, measure_qb_timing, timing_gap_report
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors

SCENARIO = "qb_hoard_12"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true", required=True)
    ap.add_argument("--drafts", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20260710)
    args = ap.parse_args()

    conn = connect()
    pool = build_pool(conn, SCENARIO)
    priors = build_slot_priors(conn)
    historical = historical_qb_timing(conn)

    measured = measure_qb_timing(
        pool, priors, n_drafts=args.drafts, base_seed=args.seed
    )
    print(timing_gap_report(measured, historical))


if __name__ == "__main__":
    main()
