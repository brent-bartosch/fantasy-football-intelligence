#!/usr/bin/env python3
"""Phase 3 exit deliverable (Task 14): regenerable EVIDENCE ASSEMBLER for
`docs/research/2026-07-10-strategy-conclusions.md`.

This script computes and emits ONLY the evidence tables + agreement stats --
the numbers a rational manager's draft policy is grounded in. The JUDGMENTS
(what to DO, with what confidence) are HAND-WRITTEN in the doc and deliberately
NOT produced here: keeping the human-written conclusions and the machine-
generated evidence in one file, with the evidence block delimited by
`<!-- BEGIN GENERATED ... -->` / `<!-- END GENERATED -->` markers, means
re-running this script refreshes the tables in place without touching a single
word of the surrounding analysis.

What it assembles (all from `sim.*`, today's farm run + the backtest machinery):
  1. Farm QB-policy table (qb_subgrid, defk=14) -- all-play% by qb_plan x scenario.
  2. Farm DEF/K table (main grid) -- all-play% by defk_round.
  3. Farm tier-break delta (main grid).
  4. R7 sim-vs-backtest agreement: the farm's 6 QB plans at defk=18 (tier_break=0.0,
     to match the backtest's default) vs the SAME 6 plans re-run through the
     `ffi.sim.backtest` machinery (3 seasons x 100 seeded drafts, scored on ACTUAL
     nflverse points), Spearman over the two 6-plan orderings.

R7 note (why option (a)): Task 11's 4 ADR-D7 reference strategies differ only in
`qb_by_round`, which never binds under qb_hoard_12 VORP (the delay knob is
`qb_not_before`, added AFTER the reference ran) -- so the persisted backtest cells
do NOT differentiate QB timing and a farm-vs-reference correlation would be
degenerate. Instead we re-run the backtest IN MEMORY with the farm's actual
(qb_not_before, qb_by_round) plans at defk=18. This writes NOTHING to
`sim.batches`/`sim.backtest_reference` -- the ADR D7 reference is untouched.

Evaluator-integrity constraints honored (from this phase's reviews):
  - Farm ABSOLUTE all-play% is MC-inflated (farm ~0.64-0.73 vs backtest ~0.53);
    only CROSS-CELL DELTAS are cited. `top3_rate` is saturated and is NOT emitted.
  - The R7 backtest neutralizes DEF (all-zero) and borrows K from the 2026 pool;
    it differentiates QB timing ONLY. Reported as-is.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import statistics
from collections import defaultdict

from scipy.stats import spearmanr

from ffi.db import connect
from ffi.sim.backtest import (
    BACKTEST_SEASONS,
    cell_base_seed,
    load_backtest_pool,
    load_points_lookup,
    run_cell,
)
from ffi.sim.priors import build_slot_priors
from ffi.sim.strategy import StrategyParams

DOC_PATH = pathlib.Path("docs/research/2026-07-10-strategy-conclusions.md")
BEGIN_MARKER = "<!-- BEGIN GENERATED EVIDENCE (scripts/strategy_conclusions.py) -->"
END_MARKER = "<!-- END GENERATED EVIDENCE -->"

# The farm's 6 QB plans, verbatim from scripts/run_sim_farm.py QB_PLANS
# (qb_not_before, qb_by_round). qb_not_before is the real delay knob.
QB_PLANS = [
    ((1, 1, 1), (1, 4, 9)),
    ((1, 3, 6), (2, 5, 9)),
    ((2, 5, 9), (3, 6, 10)),
    ((3, 6, 10), (4, 8, 12)),
    ((1, 2, 4), (2, 4, 6)),
    ((1, 4, 99), (2, 7, 19)),
]
R7_DEFK_ROUND = 18  # backtest fixes DEF at 18; compare the farm's defk=18 slice
R7_TIER_BREAK = 0.0  # backtest REF_STRATEGIES default; match the farm side to it
N_BACKTEST_DRAFTS = 100


def _fmt_pct(x) -> str:
    return f"{float(x) * 100:.1f}%"


# ---------------------------------------------------------------------------
# Farm evidence (today's kind='farm' batches)
# ---------------------------------------------------------------------------


def _farm_metric_rows(conn, date, grid, metric):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT b.strategy, r.value
               FROM sim.batches b JOIN sim.batch_results r ON r.batch_id = b.batch_id
               WHERE b.kind='farm' AND b.started_at::date=%s
                 AND b.strategy->>'grid'=%s AND r.metric=%s
                 AND b.scenario IS NOT NULL""",
            (date, grid, metric),
        )
        return cur.fetchall()


def farm_provenance(conn, date) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT b.scenario, b.data_vintage, b.git_sha
               FROM sim.batches b
               WHERE b.kind='farm' AND b.started_at::date=%s
               ORDER BY b.batch_id LIMIT 1""",
            (date,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"farm_provenance: no farm batches for {date.isoformat()}")
    _scenario, vintage, sha = row
    return {"vintage": vintage, "git_sha": sha}


def qb_policy_table(conn, date) -> list[str]:
    rows = _farm_metric_rows(conn, date, "qb_subgrid", "all_play_pct")
    se_rows = _farm_metric_rows(conn, date, "qb_subgrid", "all_play_se")
    se_by_cell = {s["cell_idx"]: float(v) for s, v in se_rows}
    lines = [
        "### Farm QB-policy (qb_subgrid, defk_round=14) -- all-play% by qb_plan x scenario",
        "",
        "_Cross-cell deltas only; absolute levels are MC-inflated (see doc caveats)._",
        "",
        "| qb_plan | qb_not_before | qb_by_round | scenario | all-play% | +/- 1.96se |",
        "|---|---|---|---|---|---|",
    ]
    for strat, val in sorted(
        rows, key=lambda t: (t[0]["qb_plan_idx"], t[0]["scenario"])
    ):
        se = se_by_cell.get(strat["cell_idx"], 0.0)
        lines.append(
            f"| {strat['qb_plan_idx']} | {tuple(strat['qb_not_before'])} | "
            f"{tuple(strat['qb_by_round'])} | {strat['scenario']} | "
            f"{_fmt_pct(val)} | +/- {_fmt_pct(1.96 * se)} |"
        )
    return lines


def defk_table(conn, date) -> list[str]:
    rows = _farm_metric_rows(conn, date, "main", "all_play_pct")
    by_defk = defaultdict(list)
    for strat, val in rows:
        by_defk[strat["defk_round"]].append(float(val))
    lines = [
        "### Farm DEF/K policy (main grid, scenario qb_hoard_12) -- all-play% by defk_round",
        "",
        "_defk_round = round at which DEF is force-drafted (K at defk_round+1) if still unheld._",
        "",
        "| defk_round | mean all-play% | n cells | delta vs earliest |",
        "|---|---|---|---|",
    ]
    ordered = sorted(by_defk)
    base = statistics.mean(by_defk[ordered[0]]) if ordered else 0.0
    for dk in ordered:
        mean = statistics.mean(by_defk[dk])
        lines.append(
            f"| {dk} | {_fmt_pct(mean)} | {len(by_defk[dk])} | "
            f"{'+' if mean - base >= 0 else ''}{_fmt_pct(mean - base)} |"
        )
    return lines


def tier_break_table(conn, date) -> list[str]:
    rows = _farm_metric_rows(conn, date, "main", "all_play_pct")
    by_tb = defaultdict(list)
    for strat, val in rows:
        by_tb[strat["tier_break_bonus"]].append(float(val))
    lines = [
        "### Farm tier-break delta (main grid)",
        "",
        "| tier_break_bonus | mean all-play% | n cells |",
        "|---|---|---|",
    ]
    ordered = sorted(by_tb)
    means = {tb: statistics.mean(by_tb[tb]) for tb in ordered}
    for tb in ordered:
        lines.append(f"| {tb} | {_fmt_pct(means[tb])} | {len(by_tb[tb])} |")
    if len(ordered) >= 2:
        delta = means[ordered[-1]] - means[ordered[0]]
        lines.append("")
        lines.append(
            f"Delta (tier_break={ordered[-1]} minus tier_break={ordered[0]}): "
            f"{'+' if delta >= 0 else ''}{_fmt_pct(delta)}"
        )
    return lines


def farm_defk18_by_plan(conn, date, tier_break=R7_TIER_BREAK) -> dict:
    """all-play% for each qb_plan at defk_round=18, tier_break fixed (default
    0.0 to match the backtest). Returns {qb_plan_idx: all_play_pct}."""
    rows = _farm_metric_rows(conn, date, "main", "all_play_pct")
    out = {}
    for strat, val in rows:
        if strat["defk_round"] == R7_DEFK_ROUND and float(
            strat["tier_break_bonus"]
        ) == float(tier_break):
            out[strat["qb_plan_idx"]] = float(val)
    return out


# ---------------------------------------------------------------------------
# R7: re-run the farm's QB plans through the backtest machinery (IN MEMORY)
# ---------------------------------------------------------------------------


def run_r7_backtest(conn) -> list[dict]:
    """For each farm QB plan, run 3 seasons x N_BACKTEST_DRAFTS seeded drafts
    through the ffi.sim.backtest machinery at defk_round=18, scored on ACTUAL
    nflverse points. Persists NOTHING (the ADR D7 reference is untouched).
    Returns one dict per plan: composite all-play%, per-season means, qb1
    round means."""
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}
    results = []
    for plan_idx, (qnb, qbr) in enumerate(QB_PLANS):
        strat = StrategyParams(
            qb_by_round=qbr, qb_not_before=qnb, defk_round=R7_DEFK_ROUND
        )
        per_season, qb1s = [], []
        for season in BACKTEST_SEASONS:
            seed = cell_base_seed(plan_idx, season)
            m = run_cell(
                pools[season],
                priors,
                lookups[season],
                strat,
                seed,
                n_drafts=N_BACKTEST_DRAFTS,
            )
            per_season.append(m["all_play_pct"])
            qb1s.append(m["qb1_round_mean"])
        results.append(
            {
                "qb_plan_idx": plan_idx,
                "qb_not_before": qnb,
                "qb_by_round": qbr,
                "composite": statistics.mean(per_season),
                "per_season": dict(zip(BACKTEST_SEASONS, per_season)),
                "qb1_round_means": dict(zip(BACKTEST_SEASONS, qb1s)),
            }
        )
    return results


def spearman_agreement(farm_by_plan: dict, backtest_by_plan: dict) -> dict:
    """Pure, testable. Spearman rank correlation between the farm and backtest
    all-play% orderings over the common qb_plan indices. Returns rho, p, the
    common plan indices (sorted), and each side's ascending rank per plan
    (rank 1 = worst)."""
    plans = sorted(set(farm_by_plan) & set(backtest_by_plan))
    if len(plans) < 3:
        raise ValueError(f"spearman_agreement: need >=3 common plans, got {len(plans)}")
    farm_vals = [farm_by_plan[p] for p in plans]
    bt_vals = [backtest_by_plan[p] for p in plans]
    rho, p = spearmanr(farm_vals, bt_vals)

    def _asc_ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0] * len(vals)
        for rank, i in enumerate(order, start=1):
            ranks[i] = rank
        return ranks

    farm_ranks = _asc_ranks(farm_vals)
    bt_ranks = _asc_ranks(bt_vals)
    return {
        "plans": plans,
        "rho": float(rho),
        "p": float(p),
        "farm_ranks": dict(zip(plans, farm_ranks)),
        "backtest_ranks": dict(zip(plans, bt_ranks)),
    }


def r7_section(farm_by_plan: dict, bt_results: list[dict]) -> list[str]:
    bt_by_plan = {r["qb_plan_idx"]: r["composite"] for r in bt_results}
    agree = spearman_agreement(farm_by_plan, bt_results and bt_by_plan)
    lines = [
        "### R7 sim-vs-backtest agreement (QB timing, defk_round=18)",
        "",
        f"Spearman rho over the {len(agree['plans'])} common QB plans: "
        f"**{agree['rho']:.3f}** (p={agree['p']:.3f}, n={len(agree['plans'])} -- "
        "low power; read the rank agreement, not the p-value).",
        "",
        "Farm side = today's main-grid all-play% at defk_round=18, "
        f"tier_break={R7_TIER_BREAK} (matched to the backtest default). "
        "Backtest side = the SAME 6 (qb_not_before, qb_by_round) plans re-run "
        f"in memory through ffi.sim.backtest: {len(BACKTEST_SEASONS)} seasons x "
        f"{N_BACKTEST_DRAFTS} seeded drafts, scored on ACTUAL nflverse points, "
        "defk_round=18, DEF neutralized. NOTHING persisted; the ADR D7 "
        "reference is untouched.",
        "",
        "| qb_plan | qb_not_before | farm all-play% | farm rank* | backtest composite | backtest rank* | bt per-season (23/24/25) | bt QB1-round |",
        "|---|---|---|---|---|---|---|---|",
    ]
    by_idx = {r["qb_plan_idx"]: r for r in bt_results}
    for plan in agree["plans"]:
        r = by_idx[plan]
        ps = r["per_season"]
        q1 = r["qb1_round_means"]
        lines.append(
            f"| {plan} | {tuple(r['qb_not_before'])} | "
            f"{_fmt_pct(farm_by_plan[plan])} | {agree['farm_ranks'][plan]} | "
            f"{_fmt_pct(r['composite'])} | {agree['backtest_ranks'][plan]} | "
            f"{'/'.join(f'{ps[s] * 100:.0f}' for s in BACKTEST_SEASONS)} | "
            f"{'/'.join(f'{q1[s]:.0f}' if q1[s] is not None else '-' for s in BACKTEST_SEASONS)} |"
        )
    lines.append("")
    lines.append(
        "_*rank 1 = worst all-play% within its own method (6 = best). Both methods "
        "independently rank qb_plan 0 (front-load QB, qb_not_before=(1,1,1)) WORST; "
        "beyond that the fine ordering diverges -- see the hand-written adjudication above._"
    )
    return lines


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_evidence_block(conn, date) -> str:
    prov = farm_provenance(conn, date)
    v = prov["vintage"]
    farm_by_plan = farm_defk18_by_plan(conn, date)
    bt_results = run_r7_backtest(conn)

    lines = [BEGIN_MARKER, ""]
    lines.append(
        f"_Generated {datetime.datetime.now().isoformat(timespec='seconds')} "
        f"from farm run dated {date.isoformat()} (git {prov['git_sha']})._"
    )
    lines.append("")
    lines.append(
        f"_Data vintage: ADP snapshot #{v.get('adp_snapshot_id')} "
        f"({v.get('adp_age_hours')}h old at farm time), valuation computed "
        f"{v.get('valuation_computed_at')}, priors latest_season "
        f"{v.get('priors_latest_season')}, degraded={v.get('degraded')}._"
    )
    lines.append("")
    lines += qb_policy_table(conn, date)
    lines.append("")
    lines += defk_table(conn, date)
    lines.append("")
    lines += tier_break_table(conn, date)
    lines.append("")
    lines += r7_section(farm_by_plan, bt_results)
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def splice_into_doc(block: str) -> None:
    """Replace the marker-delimited region in the doc with `block`, or append
    it if the doc has no markers yet. Leaves all hand-written prose intact."""
    if not DOC_PATH.exists():
        DOC_PATH.write_text(block)
        print(f"-> wrote new {DOC_PATH} (evidence block only; add judgments by hand)")
        return
    text = DOC_PATH.read_text()
    if BEGIN_MARKER in text and END_MARKER in text:
        pre = text[: text.index(BEGIN_MARKER)]
        post = text[text.index(END_MARKER) + len(END_MARKER) :]
        DOC_PATH.write_text(pre + block.rstrip("\n") + post)
    else:
        sep = "" if text.endswith("\n") else "\n"
        DOC_PATH.write_text(text + sep + "\n" + block)
    print(f"-> refreshed generated evidence block in {DOC_PATH}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, default today")
    ap.add_argument(
        "--stdout-only",
        action="store_true",
        help="print the evidence block to stdout without editing the doc",
    )
    args = ap.parse_args()
    date = (
        datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    )
    conn = connect()
    block = build_evidence_block(conn, date)
    print(block)
    if not args.stdout_only:
        splice_into_doc(block)


if __name__ == "__main__":
    main()
