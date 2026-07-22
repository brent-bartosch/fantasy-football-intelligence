#!/usr/bin/env python3
"""Nightly sim farm (Phase 3 / Task 12; v2 deploy re-center 2026-07-21): a
LIGHT sensitivity grid around the DEPLOYED v2 strategy (the A' starts-weighted
engine, `ffi.sim.strategy.DEPLOYED_PARAMS`, `pstart_weights` path). Every cell
is A' -- built by `dataclasses.replace(DEPLOYED_PARAMS, ...)` -- so the anchor
cell reproduces the live strategy EXACTLY and each sensitivity cell varies only
one still-meaningful knob:

  - qb_by_round: the QB-timing FORCE (emergent QB provably loses to a QB run, so
    v2 keeps a force). 5 plans, anchor first = (2, 5, 14).
  - defk_round:  DEF/K force timing. 3 rounds, anchor = 14.

= 5 x 3 = 15 cells at scenario qb_hoard_12. The old knobs are RETIRED under A'
and dropped from the grid (no fabricated evidence): `tier_break_bonus` and
`qb_not_before` do not affect A' scoring; `qb_tier_targets` only narrows the
rare voluntary rule-4 QB (QBs are force-driven); and the qb_hoard_0/12/24
scenarios now share one valuation (Phase B made QB replacement a fixed QB24, not
scenario-dependent), so a scenario subgrid would be three identical cells.

Per cell: one `sim.batches` row (kind='farm', git SHA, full data_vintage) + one
`sim.batch_results` row per metric + 3 `sim.sample_drafts` rows (worst/best/
random by our-seat all-play%). Metrics now include **playoff_make_pct** (H2H
12-team round-robin, our seat's rank<=6 rate over the MC seasons -- the project's
TRUSTED metric; all-play and top3 flatter under the MC evaluator). Only cross-
cell DELTAS of any metric are citable, not absolute levels (MC evaluator caveat).

ADR D2 (data-vintage / staleness): `build_data_vintage` refuses (SystemExit,
before any drafting) if the latest season-level Sleeper ADP snapshot is more
than `STALE_HOURS` old, OR if the valuation snapshot baked into
`valuation.player_value.params->>'snapshot_id'` doesn't match it.

ADR D8 (errors-only logging): no per-draft prints; exactly one summary line
per cell to stdout, plus one final wall-time line.

Seed formula (fixed, reproduces any single draft from `--base-seed`,
`cell_idx`, `draft_idx` alone): `derive_seed`.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
import pathlib
import statistics
import subprocess

import numpy as np

from ffi.db import connect
from ffi.scoring.config import load_config_v1
from ffi.sim.draft import run_draft, snake_position
from ffi.sim.opponent import DEFAULT_OPPONENT_PARAMS, ROSTER_DAMP
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import (
    REG_WEEKS,
    _build_index,
    _lineup_total,
    _mc_weekly_points,
    fit_weekly_points_cv,
)
from ffi.sim.strategy import DEPLOYED_PARAMS, StrategyParams, make_strategy_fn

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

STALE_HOURS = 36  # ADR D2 -- same threshold as morning_briefing.py / draft board

# A' sensitivity grid: vary one meaningful knob at a time around DEPLOYED.
ANCHOR_QB_BY_ROUND = DEPLOYED_PARAMS.qb_by_round  # (2, 5, 14)
ANCHOR_DEFK = DEPLOYED_PARAMS.defk_round  # 14
QB_BY_ROUND_PLANS = [
    (2, 5, 14),  # anchor = DEPLOYED (QB1 by R2, QB2 by R5, QB3 by R14)
    (1, 4, 9),  # earlier QBs / earlier forced QB3
    (2, 5, 9),  # earlier forced QB3
    (3, 6, 10),  # later QB1 (contrarian)
    (2, 7, 19),  # effectively a 2-QB build (QB3 never force-required)
]
DEFK_ROUNDS = [11, 14, 18]  # anchor = 14
SCENARIO = "qb_hoard_12"  # deployed scenario (qb_hoard_0/24 now identical)
N_DRAFTS_PER_CELL = 200
SEASONS_PER_DRAFT = 20
OUR_FRANCHISE_SLOT = 12

SEED_CELL_MULT = 1009
SEED_BASE_MULT = 100003

METRICS = (
    "all_play_pct",
    "all_play_se",
    "playoff_make_pct",  # v2: trusted H2H metric (cross-cell deltas citable)
    "playoff_make_se",
    "top3_rate",
    "qb1_round_mean",
    "def_round_mean",
)


# ---------------------------------------------------------------------------
# Grid construction
# ---------------------------------------------------------------------------


def build_grid() -> list[dict]:
    """The 15-cell A' sensitivity grid (qb_by_round x defk_round, scenario
    fixed), anchor first. `grid` labels the sensitivity axis: 'anchor' (the
    live DEPLOYED cell), 'qb_timing' (defk at anchor, qb varied), 'defk' (qb at
    anchor, defk varied), 'cross' (both off-anchor). `cell_idx` is sequential
    0-14 and, with `--base-seed`, is all `derive_seed` needs to reproduce any
    draft."""
    cells: list[dict] = []

    def add(grid, qb_by_round, defk_round):
        cells.append(
            {
                "cell_idx": len(cells),
                "grid": grid,
                "scenario": SCENARIO,
                "qb_by_round": qb_by_round,
                "defk_round": defk_round,
            }
        )

    # cell 0 = anchor (the live DEPLOYED strategy), then one-knob-at-a-time, then
    # the both-off-anchor cross cells -- so cell_idx 0 is always the reference.
    add("anchor", ANCHOR_QB_BY_ROUND, ANCHOR_DEFK)
    for qb_by_round in QB_BY_ROUND_PLANS:
        if qb_by_round != ANCHOR_QB_BY_ROUND:
            add("qb_timing", qb_by_round, ANCHOR_DEFK)
    for defk_round in DEFK_ROUNDS:
        if defk_round != ANCHOR_DEFK:
            add("defk", ANCHOR_QB_BY_ROUND, defk_round)
    for qb_by_round in QB_BY_ROUND_PLANS:
        for defk_round in DEFK_ROUNDS:
            if qb_by_round != ANCHOR_QB_BY_ROUND and defk_round != ANCHOR_DEFK:
                add("cross", qb_by_round, defk_round)
    return cells


def strategy_params_for_cell(cell: dict) -> StrategyParams:
    """A' params: DEPLOYED_PARAMS (pstart_weights + caps + qb_not_before all
    inherited) with only qb_by_round/defk_round/scenario overridden."""
    return dataclasses.replace(
        DEPLOYED_PARAMS,
        scenario=cell["scenario"],
        qb_by_round=cell["qb_by_round"],
        defk_round=cell["defk_round"],
    )


def derive_seed(base_seed: int, cell_idx: int, draft_idx: int) -> int:
    """`base_seed x 100003 + cell_idx x 1009 + draft_idx` -- collision-free
    for cell_idx in 0-14 and draft_idx in 0-199 (1009 > 199)."""
    return base_seed * SEED_BASE_MULT + cell_idx * SEED_CELL_MULT + draft_idx


def is_top3(pct_map: dict, our_position: int) -> bool:
    """True iff at most 2 of the other seats in `pct_map` have a strictly
    higher all-play pct than our seat -- i.e. our seat ranks top-3 among the
    12 for this one draft (ties don't count against us, matching the
    all-play convention elsewhere in this module)."""
    our_pct = pct_map[our_position]
    beat_us = sum(
        1 for pos, pct in pct_map.items() if pos != our_position and pct > our_pct
    )
    return beat_us <= 2


def _round_robin(n: int) -> list:
    """Circle-method 12-team round-robin schedule (11 weekly rounds of 6
    matchups). Same construction tournament_v2/qb_timing_h2h use."""
    teams, rounds = list(range(n)), []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


_RR = _round_robin(12)


def evaluate_draft(
    rosters: dict, cv_by_pos: dict, seed: int, n_seasons: int, our_position: int
) -> tuple:
    """One draft's MC evaluation, returning (all_play_map, playoff_make_pct).
    Draws weekly points ONCE (`_mc_weekly_points`, same seed as before) and
    derives both metrics from the shared (team, season, week) optimal-lineup
    totals -- so `all_play_map` is identical to `evaluate_league`'s and the H2H
    playoff-make comes free. playoff-make = fraction of the MC seasons in which
    our seat ranks top-6 in a 12-team round-robin on weekly lineup totals
    (rank = #teams with strictly more H2H wins + 1; ties go to the winner of the
    lower-indexed seat, matching backtest_p_starts.h2h_playoff)."""
    tk, flat, pos_idx = _build_index(rosters)
    n_teams = len(tk)
    points = _mc_weekly_points(flat, cv_by_pos, seed, n_seasons)  # (S,W,P)
    totals = np.stack([_lineup_total(points, pos_idx[t]) for t in tk])  # (T,S,W)

    # all-play (identical formula to evaluate_league)
    gt = totals[:, None, :, :] > totals[None, :, :, :]
    total_wins = gt.sum(axis=1).sum(axis=(1, 2))  # (T,)
    denom = (n_teams - 1) * n_seasons * REG_WEEKS
    all_play = {tk[i]: float(total_wins[i] / denom) for i in range(n_teams)}

    # H2H round-robin per season -> seasonal wins per team
    seasonal_wins = np.zeros((n_teams, n_seasons))
    for w in range(REG_WEEKS):
        for a, b in _RR[w % len(_RR)]:
            a_wins = totals[a, :, w] >= totals[b, :, w]  # (S,)
            seasonal_wins[a] += a_wins
            seasonal_wins[b] += ~a_wins
    our_ti = tk.index(our_position)
    our_wins = seasonal_wins[our_ti]  # (S,)
    rank = (seasonal_wins > our_wins[None, :]).sum(axis=0) + 1  # (S,)
    playoff_make = float((rank <= 6).mean())
    return all_play, playoff_make


# ---------------------------------------------------------------------------
# Data vintage (ADR D2 refusal)
# ---------------------------------------------------------------------------


def git_sha() -> str:
    """HEAD sha, `-dirty`-suffixed if the tree has uncommitted changes. No
    fallback: a farm row with an unattributable sha is worse than a crashed
    farm run, so a git failure propagates (CalledProcessError) rather than
    being swallowed."""
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        != ""
    )
    return f"{sha}-dirty" if dirty else sha


def build_data_vintage(conn, scenario: str, priors_latest_season: int) -> dict:
    """Refuses loud (SystemExit) BEFORE any drafting if: (a) there is no
    season-level Sleeper ADP snapshot at all, (b) it is more than
    `STALE_HOURS` old, (c) there is no valuation for `scenario`, or (d) the
    valuation's own baked-in snapshot id (`params->>'snapshot_id'`, see
    `scripts/build_valuation.py`) doesn't match the latest ADP snapshot --
    the exact mismatch Task 4's review forbade a silent blend of. Returns the
    vintage dict to store verbatim in `sim.batches.data_vintage` on success."""
    config_version = load_config_v1().version
    with conn.cursor() as cur:
        cur.execute(
            "SELECT snapshot_id, fetched_at FROM raw.sleeper_projections "
            "WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            raise SystemExit(
                "run_sim_farm: no season-level Sleeper snapshot at all -- run "
                "`uv run python scripts/ingest_sleeper.py --season 2026` first"
            )
        adp_snapshot_id, adp_fetched_at = row

        cur.execute(
            "SELECT max((params->>'snapshot_id')::int), max(computed_at) "
            "FROM valuation.player_value WHERE config_version=%s AND scenario=%s",
            (config_version, scenario),
        )
        valuation_snapshot_id, valuation_computed_at = cur.fetchone()

    if valuation_snapshot_id is None:
        raise SystemExit(
            f"run_sim_farm: no valuation.player_value rows for scenario={scenario!r} "
            "-- run `uv run python scripts/build_valuation.py` first"
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    age_hours = (now - adp_fetched_at).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        raise SystemExit(
            f"run_sim_farm: season Sleeper snapshot {age_hours:.0f}h old "
            f"(> {STALE_HOURS}h) -- refusing to build a board from stale ADP; "
            "run `uv run python scripts/ingest_sleeper.py --season 2026` first"
        )

    if valuation_snapshot_id != adp_snapshot_id:
        raise SystemExit(
            f"run_sim_farm: valuation snapshot mismatch for scenario={scenario!r} -- "
            f"valuation.player_value was built from snapshot_id={valuation_snapshot_id} "
            f"but the latest ADP snapshot is snapshot_id={adp_snapshot_id}. "
            "build_pool's ADP CTE always reads the LATEST snapshot, so a mismatch here "
            "means VORP/tier and ADP would be silently drawn from two different Sleeper "
            "pulls. Rebuild valuation first: `uv run python scripts/build_valuation.py`"
        )

    # `degraded` is always False on this return path -- any actual staleness
    # or mismatch above already raised SystemExit before a batch could be
    # persisted, per ADR D2's hard-refuse policy. The field is still carried
    # (matching sim.batches.data_vintage's documented shape) as defense in
    # depth for sim_report.py's independent nonzero-exit check, and so a
    # future softer policy (e.g. an explicit --override-stale escape hatch,
    # ADR D2's own carve-out for the draft board) has somewhere to record it
    # without a schema change.
    return {
        "adp_snapshot_id": adp_snapshot_id,
        "adp_snapshot_fetched_at": adp_fetched_at.isoformat(),
        "adp_age_hours": round(age_hours, 2),
        "valuation_snapshot_id": valuation_snapshot_id,
        "valuation_computed_at": valuation_computed_at.isoformat(),
        "priors_latest_season": priors_latest_season,
        "degraded": False,
    }


# ---------------------------------------------------------------------------
# One cell: 200 drafts -> aggregate metrics + 3 sample drafts
# ---------------------------------------------------------------------------


def _our_picks(picks: list, our_position: int) -> list:
    return [p for p in picks if p["position_slot"] == our_position]


def _first_round_of_position(our_picks: list, pos: str) -> int | None:
    matches = [p for p in our_picks if p["pos"] == pos]
    if not matches:
        return None
    return snake_position(min(p["overall"] for p in matches))[0]


def _roster_to_json(roster: list) -> list:
    return [
        {
            "ref": p.ref,
            "name": p.name,
            "position": p.position,
            "proj_points": p.proj_points,
            "vorp": p.vorp,
            "tier": p.tier,
            "adp": p.adp,
        }
        for p in roster
    ]


def run_cell(
    pool: list,
    priors,
    cv_by_pos: dict,
    strategy_params: StrategyParams,
    base_seed: int,
    cell_idx: int,
    n_drafts: int = N_DRAFTS_PER_CELL,
    n_seasons: int = SEASONS_PER_DRAFT,
    our_franchise_slot: int = OUR_FRANCHISE_SLOT,
) -> dict:
    """Run `n_drafts` seeded drafts for one cell. First pass collects only
    (seed, all_play_pct) per draft -- cheap; the worst/best/random seeds are
    then re-drafted once more each to capture their full pick log + roster,
    rather than holding all `n_drafts` full pick logs in memory at once."""
    pick_fn = make_strategy_fn(strategy_params)

    lightweight: list[tuple[int, float]] = []
    playoff_flags: list[float] = []
    top3_flags: list[bool] = []
    qb1_rounds: list[int] = []
    def1_rounds: list[int] = []

    for i in range(n_drafts):
        seed = derive_seed(base_seed, cell_idx, i)
        result = run_draft(
            pool, priors, pick_fn, seed=seed, our_franchise_slot=our_franchise_slot
        )
        pct_map, playoff_make = evaluate_draft(
            result.rosters, cv_by_pos, seed, n_seasons, result.our_position
        )
        our_pct = pct_map[result.our_position]
        lightweight.append((seed, our_pct))
        playoff_flags.append(playoff_make)
        top3_flags.append(is_top3(pct_map, result.our_position))

        our_picks = _our_picks(result.picks, result.our_position)
        qb_round = _first_round_of_position(our_picks, "QB")
        if qb_round is not None:
            qb1_rounds.append(qb_round)
        def_round = _first_round_of_position(our_picks, "DEF")
        if def_round is not None:
            def1_rounds.append(def_round)

    n = len(lightweight)
    all_play_vals = [pct for _, pct in lightweight]
    mean_pct = statistics.mean(all_play_vals)
    se_pct = statistics.stdev(all_play_vals) / math.sqrt(n) if n > 1 else 0.0
    playoff_mean = statistics.mean(playoff_flags)
    playoff_se = statistics.stdev(playoff_flags) / math.sqrt(n) if n > 1 else 0.0
    # top3_rate ranks by all-play pct (via is_top3/pct_map), not by raw PF as
    # the original plan doc phrased it -- controller-approved deviation, kept
    # consistent with all_play_pct's own ranking basis elsewhere in this module.
    top3_rate = statistics.mean(1.0 if f else 0.0 for f in top3_flags)
    qb1_round_mean = statistics.mean(qb1_rounds) if qb1_rounds else None
    def_round_mean = statistics.mean(def1_rounds) if def1_rounds else None

    worst_seed = min(lightweight, key=lambda t: t[1])[0]
    best_seed = max(lightweight, key=lambda t: t[1])[0]
    rand_rng = np.random.default_rng(derive_seed(base_seed, cell_idx, -1))
    random_seed = lightweight[int(rand_rng.integers(0, n))][0]

    samples = {}
    for reason, seed in (
        ("worst", worst_seed),
        ("best", best_seed),
        ("random", random_seed),
    ):
        result = run_draft(
            pool, priors, pick_fn, seed=seed, our_franchise_slot=our_franchise_slot
        )
        pct_map, _ = evaluate_draft(
            result.rosters, cv_by_pos, seed, n_seasons, result.our_position
        )
        samples[reason] = {
            "seed": seed,
            "all_play_pct": pct_map[result.our_position],
            "our_position": result.our_position,
            "picks": result.picks,
            "our_roster": _roster_to_json(result.rosters[result.our_position]),
        }

    return {
        "all_play_pct": mean_pct,
        "all_play_se": se_pct,
        "playoff_make_pct": playoff_mean,
        "playoff_make_se": playoff_se,
        "top3_rate": top3_rate,
        "qb1_round_mean": qb1_round_mean,
        "def_round_mean": def_round_mean,
        "n_drafts": n,
        "n_seasons": n_seasons,
        "samples": samples,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_cell(
    conn,
    cfg,
    cell: dict,
    strategy_params: StrategyParams,
    cell_result: dict,
    data_vintage: dict,
    opponent_params: dict,
    base_seed: int,
    sha: str,
) -> int:
    strategy_json = {
        **dataclasses.asdict(strategy_params),
        "cell_idx": cell["cell_idx"],
        "grid": cell["grid"],
    }
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sim.batches
               (kind, git_sha, config_version, scenario, season, strategy,
                opponent_params, n_drafts, seasons_per_draft, base_seed,
                data_vintage, finished_at)
               VALUES ('farm',%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s, now())
               RETURNING batch_id""",
            (
                sha,
                cfg.version,
                cell["scenario"],
                json.dumps(strategy_json),
                json.dumps(opponent_params),
                cell_result["n_drafts"],
                cell_result["n_seasons"],
                base_seed,
                json.dumps(data_vintage),
            ),
        )
        batch_id = cur.fetchone()[0]

        for metric in METRICS:
            val = cell_result.get(metric)
            if val is not None:
                cur.execute(
                    "INSERT INTO sim.batch_results (batch_id, metric, value) "
                    "VALUES (%s,%s,%s)",
                    (batch_id, metric, val),
                )

        for reason, sample in cell_result["samples"].items():
            cur.execute(
                """INSERT INTO sim.sample_drafts
                   (batch_id, draft_seed, reason, our_position, all_play_pct,
                    picks, our_roster)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    batch_id,
                    sample["seed"],
                    reason,
                    sample["our_position"],
                    sample["all_play_pct"],
                    json.dumps(sample["picks"]),
                    json.dumps(sample["our_roster"]),
                ),
            )
    conn.commit()
    return batch_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-seed", type=int, required=True)
    ap.add_argument(
        "--cells", type=int, default=None, help="dev cap: only run the first N cells"
    )
    ap.add_argument(
        "--n-drafts", type=int, default=N_DRAFTS_PER_CELL, help="drafts per cell"
    )
    ap.add_argument(
        "--n-seasons", type=int, default=SEASONS_PER_DRAFT, help="MC seasons per draft"
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="fast end-to-end check: 3 cells x 12 drafts x 5 MC seasons",
    )
    args = ap.parse_args()

    n_drafts = args.n_drafts
    n_seasons = args.n_seasons
    n_cells = args.cells
    if args.smoke:
        n_cells, n_drafts, n_seasons = 3, 12, 5

    conn = connect()
    cfg = load_config_v1()

    cells = build_grid()
    if n_cells is not None:
        cells = cells[:n_cells]

    priors = build_slot_priors(conn)
    cv_by_pos = fit_weekly_points_cv(conn)

    scenarios = sorted({c["scenario"] for c in cells})
    vintage_by_scenario = {
        s: build_data_vintage(conn, s, priors.latest_season) for s in scenarios
    }
    pool_by_scenario = {s: build_pool(conn, s) for s in scenarios}

    # Provenance for sim.batches.opponent_params: the full OpponentParams the
    # farm actually drafts under (run_cell -> run_draft with no explicit
    # opponent_params, so DEFAULT_OPPONENT_PARAMS applies -- now the Task 4
    # calibrated QB pos_need_scale), plus the roster-damp table and priors
    # params that are also part of the opponent model but live outside the
    # dataclass. `pos_need_scale` (a tuple of tuples) JSON-serializes to nested
    # arrays.
    opponent_params = {
        **dataclasses.asdict(DEFAULT_OPPONENT_PARAMS),
        "roster_damp": ROSTER_DAMP,
        "priors": priors.params,
    }
    sha = git_sha()

    t0 = datetime.datetime.now()
    for cell in cells:
        params = strategy_params_for_cell(cell)
        pool = pool_by_scenario[cell["scenario"]]
        result = run_cell(
            pool,
            priors,
            cv_by_pos,
            params,
            args.base_seed,
            cell["cell_idx"],
            n_drafts=n_drafts,
            n_seasons=n_seasons,
        )
        persist_cell(
            conn,
            cfg,
            cell,
            params,
            result,
            vintage_by_scenario[cell["scenario"]],
            opponent_params,
            args.base_seed,
            sha,
        )
        print(
            f"cell {cell['cell_idx']:02d}/{len(cells)} grid={cell['grid']} "
            f"qb_by_round={cell['qb_by_round']} defk={cell['defk_round']}: "
            f"all_play={result['all_play_pct']:.4f} "
            f"playoff_make={result['playoff_make_pct']:.4f} "
            f"(se {result['playoff_make_se']:.4f}) top3={result['top3_rate']:.3f}"
        )

    elapsed = (datetime.datetime.now() - t0).total_seconds()
    print(f"farm run complete: {len(cells)} cells, {elapsed:.1f}s wall")


if __name__ == "__main__":
    main()
