#!/usr/bin/env python3
"""Draft diagnostic: run ONE sim draft from our seat and print an inspectable
transcript so a human can eyeball whether the board, the opponent behavior, and
the assistant's recommendations look right BEFORE trusting any of it.

Supersedes the throwaway ``scripts/sample_draft.py``. Read-only: reuses the
already-tested ``build_pool`` / ``run_draft`` / ``evaluate_league`` /
``recommend`` machinery and only adds rendering + two small helpers (ADP delta,
roster grade). It never touches the live pick path.

Five sections:
  1. Full 19-round transcript, every pick tagged with ADP reach/value color.
  2. The assistant's recommendation at each of OUR picks (primary + rule +
     top-3 by position + notes) -- the exact ``recommend()`` view we'd see live.
  3. All-12 draft grade: each team's league-adjusted roster VORP + all-play %.
  4. Our roster detail + positional breakdown.
  5. QB-run realism check: QBs gone by end of R3/R5 this draft vs the real
     league's recent actuals (evidence that the opponent QB volume is sane).

Usage:
  uv run python scripts/draft_diagnostic.py [--config tuned|standard]
      [--seed N] [--position N] [--no-full]
"""
import argparse
import os
from collections import Counter
from pathlib import Path

# Load .env (same pattern as the rest of scripts/).
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from ffi.db import connect
from ffi.scoring.config import load_config_v1
from ffi.draft.recommend import recommend
from ffi.sim.draft import run_draft, snake_position
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import evaluate_league, fit_weekly_points_cv
from ffi.sim.strategy import StrategyParams, make_strategy_fn

# Two named configs so the diagnostic can compare. "tuned" = defaults (K:1/DEF:1
# caps, qb_by_round=(2,5,9)) plus the Phase-4 winning QB knob qb_tier_targets=
# (1,2,99): QB1 from tier 1, QB2 from tier 2, QB3+ unrestricted. "standard" =
# the old sample_draft config (VORP-driven QB, no tier targeting).
CONFIGS = {
    "tuned": StrategyParams(qb_tier_targets=(1, 2, 99)),
    "standard": StrategyParams(
        qb_not_before=(1, 1, 1), qb_by_round=(1, 4, 9), defk_round=14
    ),
}

# Real-league actuals for the realism check (from draft_picks, 2021-2025).
# QBs gone by the end of round N, averaged over the two most recent seasons
# (2024-2025), which are the QB-most-aggressive and most relevant.
REAL_QB_BY_R3 = 19  # 2024:19, 2025:19
REAL_QB_BY_R5 = 23  # 2024:24, 2025:23


def _adp_tag(player) -> str:
    """Reach/value color for one pick: compare its overall to its adp_2qb."""
    if player is None or player.adp is None:
        return "adp   -"
    return f"adp {player.adp:>4.0f}"


def _adp_delta(overall: int, player) -> str:
    if player is None or player.adp is None:
        return ""
    delta = overall - player.adp  # <0: taken before ADP (reach); >0: fell (value)
    if abs(delta) < 1:
        return "(on adp)"
    return f"(reach {abs(delta):.0f})" if delta < 0 else f"(value {abs(delta):.0f})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", choices=CONFIGS, default="tuned")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--position",
        type=int,
        default=None,
        help="pin our DRAFT seat (1-12); default = random per seed",
    )
    ap.add_argument(
        "--no-full",
        action="store_true",
        help="print only the first 3 rounds of the transcript",
    )
    args = ap.parse_args()

    params = CONFIGS[args.config]

    conn = connect()
    load_config_v1()
    pool = build_pool(conn, params.scenario)
    priors = build_slot_priors(conn)
    cv_by_pos = fit_weekly_points_cv(conn)
    by_ref = {p.ref: p for p in pool}

    # Wrap the strategy fn so we capture the recommend() view at each of OUR
    # picks (in draft order). primary == the pick taken, by the consistency
    # contract, so recording the Recommendation captures everything shown live.
    base_fn = make_strategy_fn(params)
    our_recs = []  # in overall order: (round_, Recommendation)

    def logging_pick_fn(avail_by_pos, round_, counts, picks_left_after):
        rec = recommend(avail_by_pos, round_, counts, picks_left_after, params)
        our_recs.append((round_, rec))
        return base_fn(avail_by_pos, round_, counts, picks_left_after)

    result = run_draft(
        pool,
        priors,
        logging_pick_fn,
        seed=args.seed,
        our_franchise_slot=12,
        our_position=args.position,
    )
    ours = result.our_position

    print("=" * 70)
    print(f"DRAFT DIAGNOSTIC  config={args.config}  seed={args.seed}")
    print(
        f"scenario={params.scenario}  qb_by_round={params.qb_by_round}  "
        f"qb_tier_targets={params.qb_tier_targets or '(none)'}  "
        f"defk_round={params.defk_round}"
    )
    print(f"our franchise slot 12 -> draft seat {ours}   pool={len(pool)} players")
    print("VONA: off in this view (recommendations show value + tier notes only)")
    print("=" * 70)

    # --- Section 1: full transcript -------------------------------------
    picks = sorted(result.picks, key=lambda p: p["overall"])
    cutoff = 36 if args.no_full else len(picks)
    print(
        f"\n{'-'*70}\n1. TRANSCRIPT (reach = taken before adp_2qb; value = fell past it)\n{'-'*70}"
    )
    last_round = 0
    for p in picks[:cutoff]:
        rnd = snake_position(p["overall"])[0]
        if rnd != last_round:
            print(f"\n  -- Round {rnd} --")
            last_round = rnd
        player = by_ref.get(p.get("ref"))
        mine = " <== OUR PICK" if p.get("position_slot") == ours else ""
        print(
            f"  {p['overall']:>3} {p['pos']:>3} T{p['position_slot']:>2} "
            f"{p['name']:<26} {_adp_tag(player):>8} {_adp_delta(p['overall'], player):<11}{mine}"
        )

    # --- Section 2: recommendation at each of our picks -----------------
    our_picks = [p for p in picks if p.get("position_slot") == ours]
    print(
        f"\n{'-'*70}\n2. RECOMMENDATION AT EACH OF OUR PICKS (the live assistant view)\n{'-'*70}"
    )
    for (rnd, rec), pick in zip(our_recs, our_picks):
        took = by_ref.get(pick.get("ref"))
        match = "OK" if took and rec.primary.ref == took.ref else "!! MISMATCH"
        print(
            f"\n  R{rnd:>2} pick {pick['overall']:>3}: recommend {rec.primary.position} "
            f"{rec.primary.name} (rule={rec.rule}, vorp {rec.primary.vorp:.1f})  [{match}]"
        )
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            cands = rec.by_position.get(pos, ())
            if cands:
                names = ", ".join(f"{c.name}({c.vorp:.0f})" for c in cands[:3])
                print(f"       {pos:>3}: {names}")
        if rec.notes:
            for note in rec.notes:
                print(f"       * {note}")

    # --- Section 3: all-12 draft grade ----------------------------------
    pct = evaluate_league(
        result.rosters, cv_by_pos=cv_by_pos, seed=args.seed, n_seasons=20
    )
    grades = []
    for seat, roster in result.rosters.items():
        vorp_total = sum(pl.vorp for pl in roster)
        grades.append((seat, vorp_total, pct[seat]))
    grades.sort(key=lambda g: g[2], reverse=True)
    print(
        f"\n{'-'*70}\n3. ALL-12 DRAFT GRADE (league-adjusted roster VORP + all-play %)\n{'-'*70}"
    )
    print(f"  {'rank':>4} {'seat':>4} {'roster VORP':>12} {'all-play %':>11}")
    for i, (seat, vorp_total, win) in enumerate(grades, 1):
        mine = " <== US" if seat == ours else ""
        print(f"  {i:>4} {seat:>4} {vorp_total:>12.1f} {win:>10.1%}{mine}")

    # --- Section 4: our roster ------------------------------------------
    our_roster = result.rosters[ours]
    print(f"\n{'-'*70}\n4. OUR ROSTER (seat {ours})\n{'-'*70}")
    overall_by_ref = {p["ref"]: p["overall"] for p in our_picks}
    for pl in sorted(our_roster, key=lambda x: overall_by_ref.get(x.ref, 999)):
        ov = overall_by_ref.get(pl.ref, "?")
        rnd = snake_position(ov)[0] if isinstance(ov, int) else "?"
        print(
            f"  R{rnd:>2} pick {ov:>3}  {pl.position:>3}  {pl.name:<26} vorp {pl.vorp:>7.1f}"
        )
    print(f"\n  breakdown: {dict(Counter(pl.position for pl in our_roster))}")

    # --- Section 5: QB-run realism check --------------------------------
    qb_r3 = sum(
        1 for p in picks if p["pos"] == "QB" and snake_position(p["overall"])[0] <= 3
    )
    qb_r5 = sum(
        1 for p in picks if p["pos"] == "QB" and snake_position(p["overall"])[0] <= 5
    )
    qb_total = sum(1 for p in picks if p["pos"] == "QB")
    print(
        f"\n{'-'*70}\n5. QB-RUN REALISM CHECK (this draft vs real league 2024-25 avg)\n{'-'*70}"
    )
    print(f"  QBs gone by end of R3: {qb_r3:>3}   (real league avg: {REAL_QB_BY_R3})")
    print(f"  QBs gone by end of R5: {qb_r5:>3}   (real league avg: {REAL_QB_BY_R5})")
    print(f"  QBs drafted total:     {qb_total:>3}   (real league: 39-49)")
    print()


if __name__ == "__main__":
    main()
