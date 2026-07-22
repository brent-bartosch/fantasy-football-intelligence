#!/usr/bin/env python3
"""Nightly sim-farm ADVERSARIAL report (Phase 3 / Task 12): reads the day's
`sim.batches` (kind='farm') + `sim.batch_results` + `sim.sample_drafts` rows
written by `scripts/run_sim_farm.py` and writes `reports/sim-farm-YYYY-MM-DD.md`.

ADR D5 mandate: the report's header carries its own data-vintage line (snapshot
id/age, valuation timestamp, priors latest season, git SHA, degraded flags) so
a stale-input farm run can't masquerade as fresh strategy evidence.

Sections (brief order): data-vintage header; strategy sensitivity grid
(playoff-make% + all-play% per A' cell, each cell's playoff-make DELTA vs the
DEPLOYED anchor -- the citable signal; absolute levels are MC-inflated);
worst-drafts narrative (pick-by-pick, from the stored 'worst' sample_drafts);
assumption audit (sim league-wide QB1-round mean vs historical, sim
position-share by round band vs priors -- from a uniform opponent sample).

Exits nonzero if any of today's batches carries `data_vintage.degraded=true`
(a stale/mismatched run should never be read as fresh strategy evidence).
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
from collections import defaultdict

from ffi.db import connect
from ffi.sim.calibrate import (
    _seasons_weighted_mean,
    historical_qb_timing,
    measure_qb_timing,
)
from ffi.sim.draft import snake_position
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors

REPORTS_DIR = pathlib.Path("reports")

# The assumption audit is now a HARD regression check on the adopted Task 4 QB
# calibration: it draws a uniform, unbiased 100-draft opponent sample under the
# live pool/priors (opponent_params=None -> the shipped calibrated default) and
# compares the opponent QB1-round mean against the live seasons-weighted
# historical mean. Post-calibration it must PASS; a miss exits nonzero (not a
# WARN). The old estimate was read off outcome-biased sample_drafts against a
# hardcoded 1.83 -- both replaced.
AUDIT_SCENARIO = "qb_hoard_12"
AUDIT_N_DRAFTS = 100
QB1_TOLERANCE = 0.5

_BATCHES_QUERY = """
SELECT b.batch_id, b.scenario, b.strategy, b.data_vintage, b.git_sha,
       coalesce(jsonb_object_agg(r.metric, r.value) FILTER (WHERE r.metric IS NOT NULL), '{}'::jsonb) AS metrics
FROM sim.batches b
LEFT JOIN sim.batch_results r ON r.batch_id = b.batch_id
WHERE b.kind = 'farm' AND b.started_at::date = %s
GROUP BY b.batch_id, b.scenario, b.strategy, b.data_vintage, b.git_sha
"""

_SAMPLE_DRAFTS_QUERY = """
SELECT sd.batch_id, sd.reason, sd.our_position, sd.all_play_pct, sd.picks
FROM sim.sample_drafts sd
JOIN sim.batches b ON b.batch_id = sd.batch_id
WHERE b.kind = 'farm' AND b.started_at::date = %s
"""


def _round_band(round_number: int) -> str:
    if round_number <= 3:
        return "R1-3"
    if round_number <= 8:
        return "R4-8"
    return "R9+"


def _fmt_pct(x) -> str:
    return f"{float(x) * 100:.1f}%"


def load_batches(conn, date: datetime.date) -> list:
    with conn.cursor() as cur:
        cur.execute(_BATCHES_QUERY, (date,))
        rows = cur.fetchall()
    batches = []
    for batch_id, scenario, strategy, data_vintage, sha, metrics in rows:
        batches.append(
            {
                "batch_id": batch_id,
                "scenario": scenario,
                "strategy": strategy,
                "data_vintage": data_vintage,
                "git_sha": sha,
                "metrics": {k: float(v) for k, v in metrics.items()},
            }
        )
    batches.sort(key=lambda b: b["strategy"].get("cell_idx", 0))
    return batches


def load_sample_drafts(conn, date: datetime.date) -> list:
    with conn.cursor() as cur:
        cur.execute(_SAMPLE_DRAFTS_QUERY, (date,))
        rows = cur.fetchall()
    return [
        {
            "batch_id": batch_id,
            "reason": reason,
            "our_position": our_position,
            "all_play_pct": float(all_play_pct),
            "picks": picks,
        }
        for batch_id, reason, our_position, all_play_pct, picks in rows
    ]


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _vintage_header(batches: list) -> list[str]:
    lines = ["## Data vintage", ""]
    seen_scenarios = set()
    any_degraded = False
    for b in batches:
        scenario = b["scenario"]
        if scenario in seen_scenarios:
            continue
        seen_scenarios.add(scenario)
        v = b["data_vintage"]
        degraded = bool(v.get("degraded"))
        any_degraded = any_degraded or degraded
        mark = "DEGRADED" if degraded else "OK"
        lines.append(
            f"- [{mark}] scenario={scenario}: ADP snapshot #{v.get('adp_snapshot_id')} "
            f"({v.get('adp_age_hours')}h old), valuation computed_at="
            f"{v.get('valuation_computed_at')}, priors latest_season="
            f"{v.get('priors_latest_season')}"
        )
    git_sha = batches[0]["git_sha"] if batches else None
    lines.append(f"- git SHA: {git_sha}")
    if any_degraded:
        lines.append("- **DEGRADED: at least one batch ran stale/mismatched today.**")
    return lines


def _strategy_grid_table(batches: list) -> list[str]:
    """The v2 A' sensitivity grid: one row per cell, playoff-make% (the trusted
    H2H metric) and all-play% each with +/-1.96*se, plus each cell's playoff-make
    DELTA vs the anchor (the live DEPLOYED strategy). Absolute levels are MC-
    inflated (see evaluator caveat); the citable signal is the delta column."""
    lines = ["## Strategy sensitivity grid (A' engine, anchored on DEPLOYED)", ""]
    if not batches:
        lines.append("_(no farm cells found for this date)_")
        return lines
    anchor = next((b for b in batches if b["strategy"].get("grid") == "anchor"), None)
    anchor_pm = anchor["metrics"].get("playoff_make_pct") if anchor else None
    lines.append(
        "| cell | axis | qb_by_round | defk | playoff-make% +/-CI | "
        "Δ vs anchor | all-play% +/-CI |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for b in sorted(batches, key=lambda b: b["strategy"].get("cell_idx", 0)):
        s, m = b["strategy"], b["metrics"]
        pm = m.get("playoff_make_pct")
        pm_ci = 1.96 * m.get("playoff_make_se", 0.0)
        ap = m.get("all_play_pct")
        ap_ci = 1.96 * m.get("all_play_se", 0.0)
        delta = (
            "anchor"
            if s.get("grid") == "anchor"
            else (
                f"{(pm - anchor_pm) * 100:+.1f}pp"
                if (pm is not None and anchor_pm is not None)
                else "n/a"
            )
        )
        pm_s = f"{_fmt_pct(pm)} +/- {_fmt_pct(pm_ci)}" if pm is not None else "n/a"
        ap_s = f"{_fmt_pct(ap)} +/- {_fmt_pct(ap_ci)}" if ap is not None else "n/a"
        lines.append(
            f"| {s.get('cell_idx')} | {s.get('grid')} | "
            f"{tuple(s.get('qb_by_round', ()))} | {s.get('defk_round')} | "
            f"{pm_s} | {delta} | {ap_s} |"
        )
    lines.append("")
    lines.append(
        "_playoff-make% is the project's trusted metric, but its ABSOLUTE level "
        "is MC-inflated (no bust/injury variance -- our VORP-argmax seat makes "
        "the top-6 in nearly every simulated season). Cite only the **Δ vs "
        "anchor** column (does this qb_by_round/defk_round beat the deployed "
        "cell), never the absolute rate. all-play% and top3_rate flatter for the "
        "same reason._"
    )
    return lines


def _narrative_for_sample(sample: dict) -> list[str]:
    picks = sample["picks"]
    our_position = sample["our_position"]
    our_picks = sorted(
        (p for p in picks if p["position_slot"] == our_position),
        key=lambda p: p["overall"],
    )
    lines = [
        f"  our picks (first 8 rounds), all-play%={_fmt_pct(sample['all_play_pct'])}:"
    ]
    for p in our_picks[:8]:
        rnd, _ = snake_position(p["overall"])
        lines.append(f"    R{rnd}: {p['name']} ({p['pos']})")
    other_qb_early = sum(
        1
        for p in picks
        if p["position_slot"] != our_position
        and p["pos"] == "QB"
        and snake_position(p["overall"])[0] <= 3
    )
    our_qb_early = sum(
        1
        for p in our_picks
        if p["pos"] == "QB" and snake_position(p["overall"])[0] <= 3
    )
    if other_qb_early >= 8 and our_qb_early == 0:
        lines.append(
            f"    NOTE: {other_qb_early} QBs taken by OTHER teams in rounds 1-3 while "
            "we took none -- a QB run likely cost VORP here"
        )
    return lines


def _worst_drafts_section(batches: list, sample_drafts: list, n: int = 3) -> list[str]:
    by_batch = {b["batch_id"]: b for b in batches}
    worst = [s for s in sample_drafts if s["reason"] == "worst"]
    worst.sort(key=lambda s: s["all_play_pct"])
    lines = ["## Worst drafts (pick-by-pick narrative)", ""]
    if not worst:
        lines.append("_(no worst sample_drafts found for this date)_")
        return lines
    for s in worst[:n]:
        b = by_batch.get(s["batch_id"])
        strat = b["strategy"] if b else {}
        lines.append(
            f"- cell {strat.get('cell_idx')} (grid={strat.get('grid')}, "
            f"qb_plan={strat.get('qb_plan_idx')}, defk_round={strat.get('defk_round')}, "
            f"tier_break={strat.get('tier_break_bonus')}, scenario={b['scenario'] if b else '?'})"
        )
        lines.extend(_narrative_for_sample(s))
        lines.append("")
    return lines


def _band_averaged_priors(priors) -> dict:
    """band -> pos -> mean prior share across every (slot, round) whose round
    falls in that band. The convention the audit's position-share deviation
    table compares sim shares against (controller adjudication, Task 4)."""
    priors_sums: dict = defaultdict(lambda: defaultdict(float))
    priors_counts: dict = defaultdict(int)
    for (_, rnd), share in priors.pos_share.items():
        band = _round_band(rnd)
        priors_counts[band] += 1
        for pos, v in share.items():
            priors_sums[band][pos] += v
    return {
        band: {
            pos: priors_sums[band][pos] / priors_counts[band]
            for pos in priors_sums[band]
        }
        for band in priors_sums
    }


def _assumption_audit_lines(measured, priors, historical) -> tuple[list[str], bool]:
    """Pure audit logic over a uniform opponent `QbTimingMeasurement`, the
    live `priors`, and `historical` QB-timing. Returns (markdown lines, ok):
    `ok` is False iff the opponent QB1-round mean drifts more than
    `QB1_TOLERANCE` from the seasons-weighted historical mean -- a hard
    regression on the adopted Task 4 calibration (the caller exits nonzero).

    Position-share deviations compare the sim's opponent shares against
    BAND-AVERAGED PRIORS (not deviation-from-uniform) -- the convention the
    prior sample_drafts audit used, kept so the table stays comparable.
    Opponents-only throughout: `measure_qb_timing` excludes our own seat
    (slot 12), whose QB timing is the strategy knob, not organic behavior."""
    lines = ["## Assumption audit", ""]

    sim_qb1_mean = measured.league_means[0]
    hist_qb1 = _seasons_weighted_mean(historical, "qb1")
    diff = abs(sim_qb1_mean - hist_qb1)
    ok = diff <= QB1_TOLERANCE
    if ok:
        lines.append(
            f"- sim league-wide QB1-round mean {sim_qb1_mean:.2f} vs historical "
            f"{hist_qb1:.2f} (within {QB1_TOLERANCE} tolerance)"
        )
    else:
        lines.append(
            f"- REGRESSION: sim league-wide QB1-round mean {sim_qb1_mean:.2f} vs "
            f"historical {hist_qb1:.2f} (diff {diff:.2f} > {QB1_TOLERANCE} tolerance)"
        )
        lines.append(
            f"  - _Hard regression check on the adopted Task 4 QB calibration: a "
            f"uniform {measured.n_drafts}-draft opponent sample (opponents-only, "
            "not the outcome-biased sample_drafts). sim_report exits nonzero._"
        )

    priors_avg = _band_averaged_priors(priors)
    lines.append("")
    lines.append("Sim position-share by round band vs priors (top deviations):")
    lines.append("| band | position | sim share | priors share | deviation |")
    lines.append("|---|---|---|---|---|")
    deviations = []
    for (band, pos), sim_share in measured.pos_share_by_band.items():
        prior_share = priors_avg.get(band, {}).get(pos, 0.0)
        deviations.append(
            (abs(sim_share - prior_share), band, pos, sim_share, prior_share)
        )
    deviations.sort(reverse=True)
    for dev, band, pos, sim_share, prior_share in deviations[:10]:
        lines.append(
            f"| {band} | {pos} | {sim_share:.1%} | {prior_share:.1%} | {dev:.1%} |"
        )
    return lines, ok


def _run_assumption_audit(conn, date: datetime.date) -> tuple[list[str], bool]:
    """I/O wrapper: build the live pool/priors/history off `conn` and draw the
    uniform opponent sample (`opponent_params=None` -> the shipped calibrated
    default), then hand off to `_assumption_audit_lines`. `base_seed` is
    derived from the report date so a given day's audit is reproducible.
    Fail-loud: `historical_qb_timing` raises if the mining query is empty."""
    pool = build_pool(conn, AUDIT_SCENARIO)
    priors = build_slot_priors(conn)
    historical = historical_qb_timing(conn)
    base_seed = int(date.strftime("%Y%m%d"))
    measured = measure_qb_timing(
        pool, priors, n_drafts=AUDIT_N_DRAFTS, base_seed=base_seed, opponent_params=None
    )
    return _assumption_audit_lines(measured, priors, historical)


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def render_report(conn, date: datetime.date) -> tuple[str, bool]:
    """Returns (report markdown, audit_ok). `audit_ok` is False when the
    assumption audit's QB1 regression check fails -- `main_for_date` writes the
    report either way (evidence) but exits nonzero when it is False."""
    batches = load_batches(conn, date)
    if not batches:
        raise ValueError(f"render_report: no farm batches found for {date.isoformat()}")
    sample_drafts = load_sample_drafts(conn, date)

    lines = [f"# Sim farm report -- {date.isoformat()}", ""]
    lines += _vintage_header(batches)
    lines.append("")
    lines += [
        "**Evaluator caveat (read before trusting absolute all-play% levels):** "
        "this farm scores seasons in Monte Carlo mode (`ffi.sim.season.evaluate_league`, "
        "`points_lookup=None`) -- every player's weekly points are drawn from a Gamma "
        "distribution centered on THEIR OWN `proj_points`/position-CV, with no bust/"
        "breakout variance relative to that mean and no injury-shock correlation across "
        "players. That rewards roster-level VORP optimization (our seat's argmax "
        "strategy) more generously than real outcomes do: the ADR D7 backtest gate "
        "(`run_backtests.py --gate`, scored against ACTUAL 2023-25 nflverse points) put "
        "the composite all-play% at ~0.53; if this farm's cells cluster well above that "
        "(e.g. 0.60+), treat the ABSOLUTE level as an artifact of the MC evaluator, not a "
        "real projected win rate -- the farm's cross-cell COMPARISONS (which qb_plan/"
        "defk_round/tier_break beats which other) are the trustworthy signal here, not "
        "the levels. **`top3_rate` is saturated for the same reason** (observed ~0.994 "
        "fleet-wide, i.e. our VORP-argmax seat finishes top-3 of 12 in nearly every "
        "simulated draft): the same no-bust/no-injury-shock MC variance model that "
        "inflates all-play% overrewards our seat's roster construction relative to the "
        "field, so do NOT cite `top3_rate`'s absolute value as a real top-3 probability "
        "-- only compare it across cells (which qb_plan/defk_round/tier_break has a "
        "higher or lower top3_rate than another), the same as all-play%.",
        "",
    ]
    lines += _strategy_grid_table(batches)
    lines.append("")
    lines += _worst_drafts_section(batches, sample_drafts)
    lines.append("")
    audit_lines, audit_ok = _run_assumption_audit(conn, date)
    lines += audit_lines
    return "\n".join(lines) + "\n", audit_ok


def main_for_date(conn, date: datetime.date) -> pathlib.Path:
    report, audit_ok = render_report(conn, date)
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"sim-farm-{date.isoformat()}.md"
    out.write_text(report)
    print(f"-> {out}")

    batches = load_batches(conn, date)
    degraded = any(b["data_vintage"].get("degraded") for b in batches)
    if degraded:
        raise SystemExit(
            f"sim_report: at least one batch for {date.isoformat()} ran stale/degraded"
        )
    if not audit_ok:
        raise SystemExit(
            f"sim_report: assumption audit QB1 regression for {date.isoformat()} -- "
            "opponent QB1-round mean drifted beyond tolerance from historical "
            "(adopted Task 4 calibration regressed)"
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, default today")
    args = ap.parse_args()
    date = (
        datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    )
    conn = connect()
    main_for_date(conn, date)


if __name__ == "__main__":
    main()
