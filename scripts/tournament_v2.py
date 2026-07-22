#!/usr/bin/env python3
"""Starts-Weighted Valuation v2 tournament (spec 2026-07-21).

DEPLOYED (caps) vs Engine A' vs Engine B, paired seeds, on the EXISTING
2023-25 backtest pools, scored on ACTUAL nflverse points via H2H playoff-make %
(the trusted metric). Reuses scripts/backtest_p_starts.py's H2H machinery and
scripts/swv_engines.py's engines.

  uv run python scripts/tournament_v2.py --sanity     # sanity boards only
  uv run python scripts/tournament_v2.py --seeds 100  # full tournament
  uv run python scripts/tournament_v2.py --seeds 100 --b-seeds 50  # B on fewer

Sanity gate (both engines, seeded draft on 2025): 2 QBs in first ~6 rounds,
QB3 late/absent, NO QB4, TE<=2, exactly 1 K + 1 DEF. A failing board is a
formulation bug (diagnosed via --diagnose's pick-by-pick score traces).
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffi.db import connect
from ffi.sim.backtest import BACKTEST_SEASONS, load_backtest_pool, load_points_lookup
from ffi.sim.draft import run_draft, snake_position
from ffi.sim.priors import build_slot_priors
from ffi.sim.strategy import DEPLOYED_PARAMS, make_strategy_fn

from backtest_p_starts import ci2se, h2h_playoff
from swv_engines import (
    load_p_starts,
    make_engine_a,
    make_engine_b,
    replacement_ranks,
    replacement_points,
    season_points_by_pos,
)

DEFK_ROUND = DEPLOYED_PARAMS.defk_round  # 14 -- matched across all arms
SANITY_SEED = 424242
TRACE_SEED = 900001
OUR_SLOT = 12
TABLE_PATH = Path("reports/p_starts-2026-07-21.json")
OUT_PATH = Path("reports/tournament-v2-2026-07-21.json")

STRATS = ("DEPLOYED", "A'", "B")


def build_fn(label, table, pts_by_pos, ranks, seed, n_mc, trace=None):
    """A fresh PickFn for `label` on one season's pool (+ seed for B)."""
    if label == "DEPLOYED":
        return make_strategy_fn(DEPLOYED_PARAMS)
    if label == "A'":
        return make_engine_a(table, pts_by_pos, ranks, DEFK_ROUND, trace=trace)
    if label == "B":
        return make_engine_b(
            pts_by_pos,
            ranks,
            DEFK_ROUND,
            draft_seed=seed,
            table=table,
            n_mc=n_mc,
            trace=trace,
        )
    raise ValueError(f"unknown strategy {label!r}")


def our_picks(res):
    """[(round, pos, name)] for our seat, in draft order."""
    out = []
    for p in res.picks:
        if p["position_slot"] == res.our_position:
            rnd = snake_position(p["overall"])[0]
            out.append((rnd, p["pos"], p["name"]))
    return sorted(out)


def qb_slot_ranks(res, pool):
    """ADP/proj rank (1-indexed within QB pool) of each QB we drafted -- for the
    'QB3 in the QB25-36 zone or later' check. Rank by proj_points descending."""
    qb_sorted = sorted(
        (p for p in pool if p.position == "QB"), key=lambda p: -p.proj_points
    )
    rank_of = {p.ref: i + 1 for i, p in enumerate(qb_sorted)}
    ours = [p for p in res.rosters[res.our_position] if p.position == "QB"]
    # roster order == draft order (draft appends in pick order)
    return [rank_of.get(p.ref, None) for p in ours]


def sanity_board(label, pool, priors, table, pts_by_pos, ranks, n_mc, diagnose):
    trace = [] if diagnose else None
    fn = build_fn(label, table, pts_by_pos, ranks, SANITY_SEED, n_mc, trace=trace)
    res = run_draft(pool, priors, fn, seed=SANITY_SEED, our_franchise_slot=OUR_SLOT)
    picks = our_picks(res)
    counts = Counter(p.position for p in res.rosters[res.our_position])
    seq = [pos for _, pos, _ in picks]
    qb_rounds = [r for r, pos, _ in picks if pos == "QB"]
    qbr = qb_slot_ranks(res, pool)

    # gate checks
    qb_first6 = sum(1 for r in qb_rounds if r <= 6)
    n_qb = counts.get("QB", 0)
    te = counts.get("TE", 0)
    k = counts.get("K", 0)
    dst = counts.get("DEF", 0)
    checks = {
        "2 QB in first ~6 rounds": qb_first6 >= 2,
        "QB3 late/absent (>=R7 or none)": (len(qb_rounds) < 3) or (qb_rounds[2] >= 7),
        "NO QB4": n_qb <= 3,
        "TE <= 2": te <= 2,
        "exactly 1 K": k == 1,
        "exactly 1 DEF": dst == 1,
    }
    passed = all(checks.values())

    print(f"\n=== SANITY {label} (2025 pool, seed {SANITY_SEED}) ===")
    print("  seq: " + " ".join(f"{i + 1}:{p}" for i, p in enumerate(seq)))
    print(f"  counts: {dict(counts)}")
    print(f"  QB draft rounds: {qb_rounds}  QB proj-ranks: {qbr}")
    for name, ok in checks.items():
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"  --> {'PASS' if passed else 'FAIL'}")
    if diagnose and trace is not None:
        _print_trace(trace)
    return passed, {
        "seq": seq,
        "counts": dict(counts),
        "qb_rounds": qb_rounds,
        "qb_proj_ranks": qbr,
        "checks": checks,
        "passed": passed,
    }


def _print_trace(trace):
    print("  --- pick-by-pick score trace (our picks) ---")
    for t in trace:
        ch = t["chosen"]
        print(f"  R{t['round']:>2} [{t['rule']}] -> {ch['pos']} {ch['name']}")
        for d in t["cands"][:6]:
            if "weight" in d:  # engine A'
                print(
                    f"        {d['pos']:>3} {d['name'][:22]:<22} "
                    f"score={d['score']:>9.2f}  w={d['weight']:.3f} "
                    f"proj={d['proj']:.1f} base={d['baseline']:.1f}"
                )
            else:  # engine B
                print(
                    f"        {d['pos']:>3} {d['name'][:22]:<22} "
                    f"marginal={d['score']:>9.3f}"
                )


def run_tournament(seeds, b_seeds, n_mc, diagnose, seasons):
    conn = connect()
    priors = build_slot_priors(conn)
    table = load_p_starts(TABLE_PATH)
    ranks = replacement_ranks(table)

    pools = {s: load_backtest_pool(conn, s) for s in seasons}
    lookups = {s: load_points_lookup(conn, s) for s in seasons}
    ptsbp = {s: season_points_by_pos(pools[s]) for s in seasons}

    print(
        f"TOURNAMENT SEASONS: {list(seasons)}  (BACKTEST_SEASONS in DB: "
        f"{list(BACKTEST_SEASONS)})"
    )
    print("Starts-based replacement ranks R_pos = round(12 x sum P_start):")
    for pos, r in ranks.items():
        print(f"  {pos}: sum={sum(table[pos].values()):.3f} -> R{r}")
    print("\nPer-season points_at_rank(pos, R_pos):")
    for s in seasons:
        rp = replacement_points(ptsbp[s], ranks)
        print(
            f"  {s}: "
            + "  ".join(f"{p}{ranks[p]}={rp[p]:.1f}" for p in ("QB", "RB", "WR", "TE"))
        )

    # ---- Sanity gate on 2025 for A' and B ----
    print("\n" + "=" * 70)
    print("SANITY GATE (2025 pool)")
    sanity = {}
    all_pass = True
    for label in ("A'", "B"):
        ok, info = sanity_board(
            label, pools[2025], priors, table, ptsbp[2025], ranks, n_mc, diagnose
        )
        sanity[label] = info
        all_pass = all_pass and ok
    if not all_pass:
        print(
            "\nSANITY GATE FAILED -- not running tournament. Re-run with "
            "--diagnose for score traces."
        )
        return {"sanity": sanity, "sanity_passed": False}
    print("\nSANITY GATE PASSED for both engines.")

    # ---- Tournament ----
    print("\n" + "=" * 70)
    print(f"TOURNAMENT  ({seeds} drafts/season; B on {b_seeds})\n")
    results = {}
    b_cost = None
    for label in STRATS:
        n_drafts = b_seeds if label == "B" else seeds
        per_season = {}
        pooled = []
        shape = Counter()
        n_total = 0
        t0 = time.time()
        for season in seasons:
            pool, lookup, pbp = pools[season], lookups[season], ptsbp[season]
            fn_static = None
            if label in ("DEPLOYED", "A'"):
                fn_static = build_fn(label, table, pbp, ranks, 0, n_mc)
            hits = []
            for i in range(n_drafts):
                seed = 700_000 + season * 100 + i
                fn = fn_static or build_fn(label, table, pbp, ranks, seed, n_mc)
                res = run_draft(
                    pool, priors, fn, seed=seed, our_franchise_slot=OUR_SLOT
                )
                hits.append(
                    1 if h2h_playoff(res.rosters, lookup, res.our_position) else 0
                )
                for p in res.rosters[res.our_position]:
                    shape[p.position] += 1
                n_total += 1
            per_season[season] = hits
            pooled.extend(hits)
        elapsed = time.time() - t0
        if label == "B":
            b_cost = elapsed / max(1, n_total)
        comp_p, comp_band = ci2se(pooled)
        results[label] = {
            "n_drafts_per_season": n_drafts,
            "per_season": {
                s: {"p": ci2se(h)[0], "band2se": ci2se(h)[1], "n": len(h)}
                for s, h in per_season.items()
            },
            "composite": {"p": comp_p, "band2se": comp_band, "n": len(pooled)},
            "positional_counts": {
                p: shape[p] / n_total for p in ("QB", "RB", "WR", "TE", "K", "DEF")
            },
            "elapsed_sec": elapsed,
        }

    # ---- Human table ----
    print(
        f"{'strategy':>10} | "
        + " ".join(f"{s:>13}" for s in seasons)
        + f" | {'composite +/-2SE':>20} | {'QB/RB/WR/TE/K/DEF':>22}"
    )
    print("-" * 96)
    for label in STRATS:
        r = results[label]
        cells = " ".join(
            f"{r['per_season'][s]['p']:>5.1%}+/-{r['per_season'][s]['band2se']:>4.1%}"
            for s in seasons
        )
        c = r["composite"]
        pc = r["positional_counts"]
        shape = "/".join(f"{pc[p]:.1f}" for p in ("QB", "RB", "WR", "TE", "K", "DEF"))
        print(
            f"{label:>10} | {cells} | {c['p']:>8.1%} +/-{c['band2se']:>6.1%}   "
            f"| {shape:>22}"
        )

    # composite CI comparison
    print("\nComposite CIs (2SE):")
    for label in STRATS:
        c = results[label]["composite"]
        print(
            f"  {label:>10}: [{c['p'] - c['band2se']:.1%}, {c['p'] + c['band2se']:.1%}]"
        )
    if b_cost is not None:
        print(
            f"\nEngine B compute cost: {b_cost:.2f} s/draft "
            f"({b_cost * seeds * len(seasons) / 60:.1f} min for a full "
            f"{seeds}x{len(seasons)}-draft run)"
        )

    # ---- Traces: one seeded pick-by-pick per strategy (2025 pool) ----
    traces = {}
    for label in STRATS:
        tr = [] if label != "DEPLOYED" else None
        fn = build_fn(label, table, ptsbp[2025], ranks, TRACE_SEED, n_mc, trace=tr)
        res = run_draft(
            pools[2025], priors, fn, seed=TRACE_SEED, our_franchise_slot=OUR_SLOT
        )
        traces[label] = {
            "seed": TRACE_SEED,
            "season": 2025,
            "picks": [{"round": r, "pos": p, "name": n} for r, p, n in our_picks(res)],
            "score_trace": tr,
        }

    out = {
        "generated": "2026-07-21",
        "metric": "H2H playoff-make % on actual nflverse points",
        "seasons": list(seasons),
        "seeds_per_season": seeds,
        "b_seeds_per_season": b_seeds,
        "n_mc_per_eval": n_mc,
        "defk_round": DEFK_ROUND,
        "replacement_ranks": ranks,
        "p_start_sums": {p: sum(table[p].values()) for p in table if p != "_meta"},
        "sanity": sanity,
        "sanity_passed": True,
        "b_cost_sec_per_draft": b_cost,
        "results": results,
        "traces": traces,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT_PATH}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--seeds", type=int, default=100, help="drafts/season (DEPLOYED,A')"
    )
    ap.add_argument(
        "--b-seeds",
        type=int,
        default=None,
        help="drafts/season for B (default = --seeds)",
    )
    ap.add_argument("--n-mc", type=int, default=300, help="MC seasons per B eval")
    ap.add_argument(
        "--seasons",
        default="2023,2024,2025",
        help="comma-sep season scope (default = task-scoped 2023-25; "
        "'all' = every BACKTEST_SEASONS pool in the DB)",
    )
    ap.add_argument("--sanity", action="store_true", help="sanity boards only")
    ap.add_argument("--diagnose", action="store_true", help="print score traces")
    args = ap.parse_args()
    b_seeds = args.b_seeds if args.b_seeds is not None else args.seeds
    if args.seasons == "all":
        seasons = tuple(BACKTEST_SEASONS)
    else:
        seasons = tuple(int(x) for x in args.seasons.split(","))

    if args.sanity:
        conn = connect()
        priors = build_slot_priors(conn)
        table = load_p_starts(TABLE_PATH)
        ranks = replacement_ranks(table)
        pool = load_backtest_pool(conn, 2025)
        pbp = season_points_by_pos(pool)
        print("Replacement ranks:", ranks)
        for label in ("A'", "B"):
            sanity_board(
                label, pool, priors, table, pbp, ranks, args.n_mc, args.diagnose
            )
        return

    run_tournament(args.seeds, b_seeds, args.n_mc, args.diagnose, seasons)


if __name__ == "__main__":
    main()
