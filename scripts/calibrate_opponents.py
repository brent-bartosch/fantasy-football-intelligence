#!/usr/bin/env python3
"""Opponent QB-timing calibration CLI (Phase 4 Tasks 2 + 4).

`--measure` runs the uniform-sample measurement harness (`ffi.sim.calibrate`)
against the live DB's current pool/priors and prints a markdown gap report
against `qb_timing_by_slot`'s historical timing. Measurement-only -- no model
change, no persistence.

`--fit` grid-searches the QB `pos_need_scale` tuple (`fit_qb_need_scale`) that
best reproduces historical QB timing, prints the trials table, the winning
params, and the ADR-D7-style acceptance verdict lines (each PASS/FAIL with
numbers), and writes the full evidence to
`reports/opponent-calibration-YYYY-MM-DD.md` (gitignored; the durable copy
lands in Task 5's research doc). Task 4 Step 4 then adopts the winner as
`OpponentParams`' shipped default.
"""
import argparse
import datetime
import pathlib
import time

from ffi.db import connect
from ffi.sim.availability import forecast_availability
from ffi.sim.calibrate import (
    _per_slot_qb1_mae,
    _seasons_weighted_mean,
    fit_qb_need_scale,
    historical_qb_timing,
    measure_qb_timing,
    timing_gap_report,
)
from ffi.sim.draft import _build_sorted_pool
from ffi.sim.opponent import OpponentParams
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors

SCENARIO = "qb_hoard_12"
REPORTS_DIR = pathlib.Path("reports")

# Acceptance bars (Task 4 brief). QB1 is the hard STOP bar: if no grid point
# lands within QB1_BAR of historical, the mechanism is mismatched -- report
# BLOCKED, do not widen the grid.
QB1_BAR = 0.25
QB23_BAR = 0.5
UNIFORM = 1.0 / 6.0


def _max_pos_share_deviation(measured, exclude_pos=()) -> float:
    return max(
        abs(share - UNIFORM)
        for (band, pos), share in measured.pos_share_by_band.items()
        if pos not in exclude_pos
    )


def run_measure(conn, drafts: int, seed: int) -> None:
    pool = build_pool(conn, SCENARIO)
    priors = build_slot_priors(conn)
    historical = historical_qb_timing(conn)
    measured = measure_qb_timing(pool, priors, n_drafts=drafts, base_seed=seed)
    print(timing_gap_report(measured, historical))


def run_fit(conn, drafts: int, seed: int) -> None:
    pool = build_pool(conn, SCENARIO)
    priors = build_slot_priors(conn)
    historical = historical_qb_timing(conn)

    h1 = _seasons_weighted_mean(historical, "qb1")
    h2 = _seasons_weighted_mean(historical, "qb2")
    h3 = _seasons_weighted_mean(historical, "qb3")

    # Task 2 baseline (un-scaled) -- same seed/drafts, for the before/after
    # per-slot table and the pos-share "not materially worse" check.
    baseline = measure_qb_timing(pool, priors, n_drafts=drafts, base_seed=seed)
    baseline_max_dev = _max_pos_share_deviation(baseline)

    best_params, trials = fit_qb_need_scale(
        pool, priors, historical, n_drafts=drafts, base_seed=seed
    )
    best = trials[0]
    best_scale = best["scale"]

    # Re-measure the winner once for the full per-slot + pos-share evidence
    # (fit_qb_need_scale only retains league means / objective per trial).
    best_measured = measure_qb_timing(
        pool,
        priors,
        n_drafts=drafts,
        base_seed=seed,
        opponent_params=OpponentParams(pos_need_scale=(("QB", best_scale),)),
    )
    best_max_dev = _max_pos_share_deviation(best_measured)
    # Substantive check: the calibration's JOB is to raise QB early-round
    # share, so QB's own deviation-from-uniform is expected to grow -- that is
    # the target, not distortion. What must NOT get materially worse is the
    # rest of the mix (RB/WR/TE/K/DEF), so the acceptance verdict is on the
    # non-QB max deviation.
    baseline_nonqb_dev = _max_pos_share_deviation(baseline, exclude_pos=("QB",))
    best_nonqb_dev = _max_pos_share_deviation(best_measured, exclude_pos=("QB",))

    qb1_ok = abs(best["qb1"] - h1) <= QB1_BAR
    qb2_ok = abs(best["qb2"] - h2) <= QB23_BAR
    qb3_ok = abs(best["qb3"] - h3) <= QB23_BAR
    posshare_ok = best_nonqb_dev <= baseline_nonqb_dev + 0.05

    lines: list[str] = []
    lines.append(
        f"# Opponent QB-timing calibration -- {datetime.date.today().isoformat()}"
    )
    lines.append("")
    lines.append(
        f"Scenario `{SCENARIO}`, {drafts} drafts/candidate, base_seed {seed}, "
        f"default grid (72 candidates)."
    )
    lines.append("")
    lines.append(
        f"Historical (seasons-weighted league means): "
        f"QB1={h1:.2f}, QB2={h2:.2f}, QB3={h3:.2f}"
    )
    lines.append(
        f"Task 2 baseline (un-scaled): QB1={baseline.league_means[0]:.2f}, "
        f"QB2={baseline.league_means[1]:.2f}, QB3={baseline.league_means[2]:.2f}, "
        f"max pos-share deviation-from-uniform={baseline_max_dev:.3f}"
    )
    lines.append("")

    lines.append("## Trials (top 10 by objective)")
    lines.append("")
    lines.append(
        "| rank | scale (s0,s1,s2) | QB1 | QB2 | QB3 | per-slot QB1 MAE | objective |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for i, t in enumerate(trials[:10], 1):
        s = t["scale"]
        lines.append(
            f"| {i} | ({s[0]:g}, {s[1]:g}, {s[2]:g}) | {t['qb1']:.2f} | "
            f"{t['qb2']:.2f} | {t['qb3']:.2f} | {t['per_slot_qb1_mae']:.2f} | "
            f"{t['objective']:.3f} |"
        )
    lines.append("")

    lines.append(f'## Winner: pos_need_scale = (("QB", {best_scale}),)')
    lines.append("")
    lines.append("## Acceptance verdicts")
    lines.append("")
    lines.append(
        f"- [{'PASS' if qb1_ok else 'FAIL'}] QB1 mean {best['qb1']:.2f} vs historical "
        f"{h1:.2f} (|delta|={abs(best['qb1'] - h1):.2f}, bar {QB1_BAR})"
    )
    lines.append(
        f"- [{'PASS' if qb2_ok else 'FAIL'}] QB2 mean {best['qb2']:.2f} vs historical "
        f"{h2:.2f} (|delta|={abs(best['qb2'] - h2):.2f}, bar {QB23_BAR})"
    )
    lines.append(
        f"- [{'PASS' if qb3_ok else 'FAIL'}] QB3 mean {best['qb3']:.2f} vs historical "
        f"{h3:.2f} (|delta|={abs(best['qb3'] - h3):.2f}, bar {QB23_BAR})"
    )
    lines.append(
        f"- [report-only] per-slot QB1 MAE = {best['per_slot_qb1_mae']:.2f} "
        "(no hard bar -- priors carry slot identity; the knob is global)"
    )
    lines.append(
        f"- [{'PASS' if posshare_ok else 'FAIL'}] non-QB max pos-share "
        f"deviation-from-uniform {best_nonqb_dev:.3f} vs baseline "
        f"{baseline_nonqb_dev:.3f} (the mix outside the QB target must not get "
        "materially worse; +0.05 tolerance)"
    )
    lines.append(
        f"- [report-only] overall max pos-share deviation {best_max_dev:.3f} vs "
        f"baseline {baseline_max_dev:.3f} -- the growth is the INTENDED QB "
        "early-round rise (R1-3 QB share is exactly what the knob lifts)"
    )
    lines.append("")

    lines.append("## Before/after per-slot QB1 (round, opponents only)")
    lines.append("")
    lines.append("| slot | baseline QB1 | calibrated QB1 | historical QB1 | n |")
    lines.append("|---|---|---|---|---|")
    for slot in sorted(best_measured.per_slot):
        b = baseline.per_slot.get(slot, {})
        c = best_measured.per_slot[slot]
        hq = historical.get(slot, {}).get("qb1")

        def _f(v):
            return "-" if v is None or (isinstance(v, float) and v != v) else f"{v:.2f}"

        lines.append(
            f"| {slot} | {_f(b.get('qb1'))} | {_f(c['qb1'])} | {_f(hq)} | {c['n']} |"
        )
    lines.append("")

    report = "\n".join(lines) + "\n"
    print(report)

    qb1_stop = any(abs(t["qb1"] - h1) <= QB1_BAR for t in trials)
    if not qb1_stop:
        print(
            "\n*** BLOCKED: no grid point met the QB1 +/-0.25 bar -- mechanism "
            "mismatch, not a tuning problem. Do NOT widen the grid. ***"
        )

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"opponent-calibration-{datetime.date.today().isoformat()}.md"
    out.write_text(report)
    print(f"-> wrote {out}")


def run_vona_smoke(conn, seed: int) -> None:
    """Manual perf smoke (Task 8 Step 3): the unit perf test bounds cost on
    the 60-player synthetic fixture, but `_avail_view` filtering and the
    `taken`-set ops scale with pool size (2,141 live vs ~342 synthetic) --
    this runs the real thing against the live DB pool/priors: 200 rollouts x
    22 synthetic upcoming picks (22 is the max opponent picks between our own
    two picks, at a snake turn-boundary seat), and prints wall time. No
    persistence -- print-only, like --measure."""
    pool = build_pool(conn, SCENARIO)
    priors = build_slot_priors(conn)
    avail_by_pos = _build_sorted_pool(pool)

    # 22 synthetic upcoming opponent picks spanning a round boundary
    # (rounds 5->6), franchise slots cycling 1-12 twice -- a plausible
    # snake-turn-boundary window, independent of any specific real draft.
    upcoming = [(((i - 1) % 12) + 1, 5 if i <= 11 else 6, {}) for i in range(1, 23)]

    start = time.perf_counter()
    forecast_availability(avail_by_pos, priors, upcoming, n_rollouts=200, seed=seed)
    elapsed = time.perf_counter() - start
    print(
        f"--vona-smoke: 200 rollouts x 22 upcoming picks over {len(pool)}-player "
        f"live pool: {elapsed:.2f}s wall"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--measure", action="store_true")
    group.add_argument("--fit", action="store_true")
    group.add_argument("--vona-smoke", action="store_true")
    ap.add_argument("--drafts", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20260710)
    args = ap.parse_args()

    conn = connect()
    if args.measure:
        run_measure(conn, args.drafts, args.seed)
    elif args.fit:
        run_fit(conn, args.drafts, args.seed)
    elif args.vona_smoke:
        run_vona_smoke(conn, args.seed)
    else:
        raise ValueError("no action selected (--measure/--fit/--vona-smoke)")


if __name__ == "__main__":
    main()
