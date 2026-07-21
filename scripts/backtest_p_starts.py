#!/usr/bin/env python3
"""Item 4 Phase A Part 2 -- go/no-go backtest for `value = VORP x P(starts)`.

Head-to-head, on ACTUAL 2023-25 points, of two OUR-seat pick functions:

  A. PROTOTYPE   rule-4 score = `vorp * P_start[pos][slot]`, slot = counts[pos]+1.
                 NO caps, NO qb_not_before, NO qb_by_round QB-deadline force,
                 NO qb_tier_targets -- QB3/TE2 discipline is meant to be EMERGENT
                 from the P(starts) weights (QB3=.259, TE2=.152, ...). Keeps the
                 feasibility force (rule 1) and the K/DEF `defk_round` force
                 (rule 3); DEF/K are acquired ONLY via that force (never rule 4),
                 so exactly one of each fills without a cap knob.
  B. DEPLOYED    `ffi.sim.strategy.DEPLOYED_PARAMS` verbatim (QB3-late R10/R14 +
                 TE cap 2) -- the hand-tuned cap-patch the prototype must beat.

Metric: H2H playoff-make % (12-team round-robin, rank<=6), the metric this
project TRUSTS -- all-play flatters (see scripts/qb_timing_h2h.py,
scripts/positional_depth.py). Same seeds drive both strategies (paired): only
the OUR-seat pick fn differs, so the slot permutation + every opponent pick are
identical, isolating the strategy effect. CIs are 2*SE of the pooled Bernoulli
playoff-make proportion over all season x seed drafts.

DECISION RULE (from the handoff): VORP x P(starts) WINS only if its composite
playoff-make CI does NOT overlap DEPLOYED's. Otherwise -> documented negative
result, caps stay. This script only REPORTS + RECOMMENDS; it does not proceed
to Phase B and does not touch the live strategy path.

CAVEAT baked into the interpretation: the backtest is BLIND to RB value (2024
RB/WR/TE projections are synthetic; QB-timing is the only real differentiator),
so any win/loss surfaces mostly through QB3/TE handling, not RB depth.

    uv run python scripts/backtest_p_starts.py [--seeds 100] [--sanity]
"""
import argparse
import json
import math
import os
import statistics
from collections import Counter
from pathlib import Path

env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from ffi.db import connect
from ffi.sim.backtest import BACKTEST_SEASONS, load_backtest_pool, load_points_lookup
from ffi.sim.draft import run_draft
from ffi.sim.opponent import CAND_WINDOW, feasible, required_picks
from ffi.sim.priors import POSITIONS, build_slot_priors
from ffi.sim.season import (
    REG_WEEKS,
    _build_index,
    _lineup_total,
    _lookup_weekly_points,
)
from ffi.sim.strategy import (
    DEPLOYED_PARAMS,
    _pick_best,
    _unmet_positions,
    make_strategy_fn,
)

# defk_round matched to DEPLOYED_PARAMS (default 14) so DEF/K timing is identical
# across both arms and can't confound the comparison. (In the backtest pools DEF
# is all-zero dummies and K is near-noise -- module docstring points 4-5 -- so
# this choice is metric-neutral either way; matching just keeps it clean.)
DEFK_ROUND = DEPLOYED_PARAMS.defk_round  # 14


# ---------------------------------------------------------------------------
# P(starts) table
# ---------------------------------------------------------------------------


def load_p_starts(path: Path) -> dict:
    """Load + validate the P_start[pos][slot] table. REFUSES a table whose
    `_meta.mode` isn't `byes+injuries` -- the mode-overwrite footgun the
    handoff flags (both estimator modes write the same dated JSON path; a
    stale byes-only table has QB3=.122 instead of .259 and would silently
    mis-weight the whole prototype). Fail loud on any malformation."""
    if not path.exists():
        raise FileNotFoundError(
            f"P(starts) table not found at {path} -- regenerate with "
            "`uv run python scripts/estimate_p_starts.py` (default = byes+injuries)"
        )
    raw = json.loads(path.read_text())
    meta = raw.get("_meta")
    if meta is None:
        raise ValueError(
            f"{path} has no `_meta` block -- regenerate with the current "
            "estimate_p_starts.py so the mode is recorded (refusing to guess)"
        )
    if meta.get("mode") != "byes+injuries":
        raise ValueError(
            f"{path} was generated in mode {meta.get('mode')!r}, not "
            "'byes+injuries' -- this is the stale-file footgun; rerun "
            "`uv run python scripts/estimate_p_starts.py` (no --no-injuries)"
        )
    table = {}
    for pos in POSITIONS:
        if pos not in raw:
            raise ValueError(f"P(starts) table {path} missing position {pos!r}")
        table[pos] = {int(slot): float(v) for slot, v in raw[pos].items()}
    return table


def p_start_weight(table: dict, pos: str, slot: int) -> float:
    """P_start for the `slot`-th player at `pos`. Slots deeper than the table
    (the estimator's DEPTH cap) clamp to the deepest known slot -- a
    conservative floor: a bench body past estimated depth ~never starts, and
    clamping avoids a 0.0 that would silently flip negative-VORP argmax."""
    row = table[pos]
    return row[min(slot, max(row))]


# ---------------------------------------------------------------------------
# Prototype PickFn: rule-4 score = vorp * P_start[pos][slot]
# ---------------------------------------------------------------------------


def make_p_starts_pick_fn(table: dict, defk_round: int = DEFK_ROUND):
    def pick_fn(avail_by_pos, round_, counts, picks_left_after):
        # Rule 1: feasibility force (unmet starter/FLEX slots only), scored by
        # the same vorp * P(starts) rule.
        if required_picks(counts) == picks_left_after:
            scored = []
            for pos in _unmet_positions(counts):
                cands = avail_by_pos.get(pos) or []
                if not cands:
                    continue
                w = p_start_weight(table, pos, counts.get(pos, 0) + 1)
                for c in cands[:CAND_WINDOW]:
                    scored.append((c.vorp * w, c))
            if scored:
                return _pick_best(scored)

        # Rule 3: DEF/K force (rule 2 QB-deadline force is intentionally DROPPED
        # -- QB timing is meant to be emergent). DEF then K, one each.
        if round_ >= defk_round and counts.get("DEF", 0) == 0:
            cands = avail_by_pos.get("DEF") or []
            if cands and feasible(counts, "DEF", picks_left_after):
                return _pick_best([(c.vorp, c) for c in cands[:CAND_WINDOW]])
        if round_ >= defk_round + 1 and counts.get("K", 0) == 0:
            cands = avail_by_pos.get("K") or []
            if cands and feasible(counts, "K", picks_left_after):
                return _pick_best([(c.vorp, c) for c in cands[:CAND_WINDOW]])

        # Rule 4: value = vorp * P_start[pos][slot], slot = counts[pos]+1.
        # No caps / no qb gating. DEF/K are EXCLUDED here -- they come only via
        # the rule-3 force, which self-limits to exactly one each; this avoids
        # the zero-VORP DEF dummy (vorp=0 * any weight = 0) beating genuine
        # negative-VORP bench bodies and drafting dead-weight 2nd DEF/K.
        scored = []
        for pos in POSITIONS:
            if pos in ("DEF", "K"):
                continue
            if not feasible(counts, pos, picks_left_after):
                continue
            cands = avail_by_pos.get(pos) or []
            if not cands:
                continue
            w = p_start_weight(table, pos, counts.get(pos, 0) + 1)
            for c in cands[:CAND_WINDOW]:
                scored.append((c.vorp * w, c))
        if not scored:
            raise ValueError(
                f"p_starts pick_fn: no feasible candidate at round {round_} "
                f"(counts={counts}, picks_left_after={picks_left_after})"
            )
        return _pick_best(scored)

    return pick_fn


# ---------------------------------------------------------------------------
# H2H playoff-make metric (mirrors qb_timing_h2h.py / positional_depth.py)
# ---------------------------------------------------------------------------


def round_robin(n):
    teams, rounds = list(range(n)), []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


ROUNDS = round_robin(12)


def h2h_playoff(rosters, lookup, our) -> bool:
    """True if OUR seat makes the 6-team playoff via a 12-team round-robin on
    ACTUAL weekly optimal-lineup totals."""
    tk, flat, pos_idx = _build_index(rosters)
    pts = _lookup_weekly_points(flat, lookup)
    wt = {t: _lineup_total(pts, pos_idx[t])[0] for t in tk}
    wins = {t: 0 for t in tk}
    for w in range(REG_WEEKS):
        for a, b in ROUNDS[w % len(ROUNDS)]:
            ta, tb = tk[a], tk[b]
            wins[ta if wt[ta][w] >= wt[tb][w] else tb] += 1
    rank = sum(1 for t in tk if wins[t] > wins[our]) + 1
    return rank <= 6


def ci2se(hits: list) -> tuple:
    """(proportion, 2*SE) for a list of 0/1 playoff-make outcomes."""
    n = len(hits)
    p = statistics.mean(hits)
    se = math.sqrt(p * (1 - p) / n) if n else 0.0
    return p, 2 * se


# ---------------------------------------------------------------------------
# Sanity check: draft one board, confirm emergent QB/TE discipline
# ---------------------------------------------------------------------------


def sanity_check(pool, priors, pick_fn):
    res = run_draft(pool, priors, pick_fn, seed=424242, our_franchise_slot=12)
    counts = Counter(p.position for p in res.rosters[res.our_position])
    picks = [
        (p["pos"], p["name"])
        for p in res.picks
        if p["position_slot"] == res.our_position
    ]
    return counts, picks


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=100, help="drafts per season")
    ap.add_argument(
        "--table",
        default="reports/p_starts-2026-07-21.json",
        help="P(starts) JSON (must be byes+injuries)",
    )
    ap.add_argument("--sanity", action="store_true", help="print the sanity draft")
    args = ap.parse_args()

    table = load_p_starts(Path(args.table))
    proto_fn = make_p_starts_pick_fn(table)
    deployed_fn = make_strategy_fn(DEPLOYED_PARAMS)

    conn = connect()
    priors = build_slot_priors(conn)
    pools = {s: load_backtest_pool(conn, s) for s in BACKTEST_SEASONS}
    lookups = {s: load_points_lookup(conn, s) for s in BACKTEST_SEASONS}

    strategies = {
        "PROTOTYPE (vorp x P_start)": proto_fn,
        "DEPLOYED (caps)": deployed_fn,
    }

    if args.sanity:
        print("=== SANITY: one prototype draft (seed 424242) ===")
        counts, picks = sanity_check(pools[2025], priors, proto_fn)
        print(f"roster counts: {dict(counts)}")
        print("picks:")
        for i, (pos, name) in enumerate(picks, 1):
            print(f"  R{i:>2} {pos:>3}  {name}")
        print()

    print(f"H2H playoff-make % (2023-25, {args.seeds} drafts/season, paired seeds)\n")
    header = f"{'strategy':>26} | " + " ".join(f"{s:>6}" for s in BACKTEST_SEASONS)
    header += f" | {'composite (CI 2SE)':>24} | {'roster QB/RB/WR/TE':>18}"
    print(header)
    print("-" * len(header))

    results = {}
    for label, fn in strategies.items():
        per_season = {}
        pooled = []
        comp_shape = Counter()
        n_drafts = 0
        for season in BACKTEST_SEASONS:
            pool, lookup = pools[season], lookups[season]
            hits = []
            for i in range(args.seeds):
                seed = 700_000 + season * 100 + i
                res = run_draft(pool, priors, fn, seed=seed, our_franchise_slot=12)
                hits.append(
                    1 if h2h_playoff(res.rosters, lookup, res.our_position) else 0
                )
                for p in res.rosters[res.our_position]:
                    comp_shape[p.position] += 1
                n_drafts += 1
            per_season[season] = hits
            pooled.extend(hits)
        comp_p, comp_band = ci2se(pooled)
        results[label] = {
            "per_season": {s: ci2se(h) for s, h in per_season.items()},
            "composite": (comp_p, comp_band),
            "pooled": pooled,
        }
        season_cells = " ".join(
            f"{ci2se(per_season[s])[0]:>6.1%}" for s in BACKTEST_SEASONS
        )
        shape = "/".join(
            f"{comp_shape[p] / n_drafts:.1f}" for p in ("QB", "RB", "WR", "TE")
        )
        print(
            f"{label:>26} | {season_cells} | "
            f"{comp_p:>10.1%} +/-{comp_band:>6.1%}      | {shape:>18}"
        )

    # Decision
    labels = list(strategies)
    proto = results[labels[0]]["composite"]
    deployed = results[labels[1]]["composite"]
    proto_lo, proto_hi = proto[0] - proto[1], proto[0] + proto[1]
    dep_lo, dep_hi = deployed[0] - deployed[1], deployed[0] + deployed[1]
    print()
    print(
        f"PROTOTYPE composite CI: [{proto_lo:.1%}, {proto_hi:.1%}]   "
        f"DEPLOYED composite CI: [{dep_lo:.1%}, {dep_hi:.1%}]"
    )
    proto_wins = proto_lo > dep_hi  # non-overlap, prototype above
    deployed_wins = dep_lo > proto_hi
    if proto_wins:
        verdict = "GO -- VORP x P(starts) beats caps (non-overlapping CIs). -> Phase B."
    elif deployed_wins:
        verdict = (
            "NO-GO -- DEPLOYED caps beat the prototype (non-overlapping). Caps stay."
        )
    else:
        verdict = (
            "NO-GO -- CIs OVERLAP: no significant edge for VORP x P(starts). "
            "Caps stay (documented negative result)."
        )
    print(f"\nDECISION: {verdict}")
    print(
        "NOTE: backtest is BLIND to RB value (synthetic 2024 RB/WR/TE projections); "
        "the signal here is QB3/TE discipline, not RB depth."
    )


if __name__ == "__main__":
    main()
