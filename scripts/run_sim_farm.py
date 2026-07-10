#!/usr/bin/env python3
"""Nightly sim farm (Phase 3 / Task 12): the 66-cell strategy grid --
QB timing (6 plans, each a (qb_not_before, qb_by_round) pair -- Task 11's
plan amendment, since qb_by_round deadlines alone never bind under
qb_hoard_12 VORP) x DEF/K round (4) x tier-break bonus (2) = 48 "main" cells
at scenario qb_hoard_12, PLUS the 6 QB plans crossed with all 3 QB-hoard
scenarios at defk_round=14/tier_break=0.0 = 18 "qb_subgrid" cells. 66 cells x
200 seeded drafts x 20 MC seasons each = 13,200 drafts/night.

Per cell: one `sim.batches` row (kind='farm', git SHA, full data_vintage --
see `build_data_vintage`) + one `sim.batch_results` row per metric
(all_play_pct, all_play_se, top3_rate, qb1_round_mean, def_round_mean) + 3
`sim.sample_drafts` rows (worst/best/random by our-seat all-play%, full
228-pick log + our 19-player roster).

ADR D2 (data-vintage / staleness): `build_data_vintage` refuses (SystemExit,
before any drafting) if the latest season-level Sleeper ADP snapshot is more
than `STALE_HOURS` old, OR if the valuation snapshot baked into
`valuation.player_value.params->>'snapshot_id'` for the scenario being built
doesn't match that latest ADP snapshot -- a silent blend of a stale
valuation against a fresher ADP board is exactly the failure mode Task 4's
review forbade (a mismatch means `build_pool`'s ADP CTE and its VORP/tier
columns would be reading two different snapshots without saying so).

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
from ffi.sim.opponent import CAND_WINDOW, ROSTER_DAMP, TAU
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import evaluate_league, fit_weekly_points_cv
from ffi.sim.strategy import StrategyParams, make_strategy_fn

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

STALE_HOURS = 36  # ADR D2 -- same threshold as morning_briefing.py / draft board

QB_PLANS = [  # (qb_not_before, qb_by_round)
    ((1, 1, 1), (1, 4, 9)),  # take QBs as VORP dictates (front-loaded)
    ((1, 3, 6), (2, 5, 9)),  # slight stagger
    ((2, 5, 9), (3, 6, 10)),  # delayed QB1 to R2, spread hoard
    ((3, 6, 10), (4, 8, 12)),  # contrarian: first QB no earlier than R3
    ((1, 2, 4), (2, 4, 6)),  # aggressive 3-QB hoard early
    ((1, 4, 99), (2, 7, 19)),  # effectively 2-QB build (third QB never required)
]
DEFK_ROUNDS = [8, 11, 14, 18]  # tests the Phase 2 DRAFT-EARLY verdicts in context
TIER_BREAK = [0.0, 8.0]
SCENARIOS_MAIN = ["qb_hoard_12"]
SCENARIOS_QB_SUBGRID = ["qb_hoard_0", "qb_hoard_12", "qb_hoard_24"]
N_DRAFTS_PER_CELL = 200
SEASONS_PER_DRAFT = 20
OUR_FRANCHISE_SLOT = 12

SEED_CELL_MULT = 1009
SEED_BASE_MULT = 100003

METRICS = (
    "all_play_pct",
    "all_play_se",
    "top3_rate",
    "qb1_round_mean",
    "def_round_mean",
)


# ---------------------------------------------------------------------------
# Grid construction
# ---------------------------------------------------------------------------


def build_grid() -> list[dict]:
    """The 66-cell grid, in deterministic build order: 48 "main" cells (QB
    plan x defk_round x tier_break, scenario fixed at qb_hoard_12), then 18
    "qb_subgrid" cells (QB plan x scenario, defk_round=14/tier_break=0.0
    fixed). `cell_idx` is assigned sequentially over this same order, 0-65,
    and is the only thing (besides `--base-seed`) `derive_seed` needs to
    reproduce any draft in the farm."""
    cells: list[dict] = []
    idx = 0
    for qb_plan_idx, (qb_not_before, qb_by_round) in enumerate(QB_PLANS):
        for defk_round in DEFK_ROUNDS:
            for tier_break in TIER_BREAK:
                cells.append(
                    {
                        "cell_idx": idx,
                        "grid": "main",
                        "scenario": SCENARIOS_MAIN[0],
                        "qb_plan_idx": qb_plan_idx,
                        "qb_not_before": qb_not_before,
                        "qb_by_round": qb_by_round,
                        "defk_round": defk_round,
                        "tier_break_bonus": tier_break,
                    }
                )
                idx += 1
    for qb_plan_idx, (qb_not_before, qb_by_round) in enumerate(QB_PLANS):
        for scenario in SCENARIOS_QB_SUBGRID:
            cells.append(
                {
                    "cell_idx": idx,
                    "grid": "qb_subgrid",
                    "scenario": scenario,
                    "qb_plan_idx": qb_plan_idx,
                    "qb_not_before": qb_not_before,
                    "qb_by_round": qb_by_round,
                    "defk_round": 14,
                    "tier_break_bonus": 0.0,
                }
            )
            idx += 1
    return cells


def strategy_params_for_cell(cell: dict) -> StrategyParams:
    return StrategyParams(
        scenario=cell["scenario"],
        qb_by_round=cell["qb_by_round"],
        qb_not_before=cell["qb_not_before"],
        defk_round=cell["defk_round"],
        tier_break_bonus=cell["tier_break_bonus"],
    )


def derive_seed(base_seed: int, cell_idx: int, draft_idx: int) -> int:
    """`base_seed x 100003 + cell_idx x 1009 + draft_idx` -- collision-free
    for cell_idx in 0-65 and draft_idx in 0-199 (1009 > 199)."""
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


# ---------------------------------------------------------------------------
# Data vintage (ADR D2 refusal)
# ---------------------------------------------------------------------------


def git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=str(REPO_ROOT)
        ).strip()
    except Exception:
        return None


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
    top3_flags: list[bool] = []
    qb1_rounds: list[int] = []
    def1_rounds: list[int] = []

    for i in range(n_drafts):
        seed = derive_seed(base_seed, cell_idx, i)
        result = run_draft(
            pool, priors, pick_fn, seed=seed, our_franchise_slot=our_franchise_slot
        )
        pct_map = evaluate_league(
            result.rosters, cv_by_pos=cv_by_pos, seed=seed, n_seasons=n_seasons
        )
        our_pct = pct_map[result.our_position]
        lightweight.append((seed, our_pct))
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
        pct_map = evaluate_league(
            result.rosters, cv_by_pos=cv_by_pos, seed=seed, n_seasons=n_seasons
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
        "top3_rate": top3_rate,
        "qb1_round_mean": qb1_round_mean,
        "def_round_mean": def_round_mean,
        "n_drafts": n,
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
    sha: str | None,
) -> int:
    strategy_json = {
        **dataclasses.asdict(strategy_params),
        "cell_idx": cell["cell_idx"],
        "grid": cell["grid"],
        "qb_plan_idx": cell["qb_plan_idx"],
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
                SEASONS_PER_DRAFT,
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
    args = ap.parse_args()

    conn = connect()
    cfg = load_config_v1()

    cells = build_grid()
    if args.cells is not None:
        cells = cells[: args.cells]

    priors = build_slot_priors(conn)
    cv_by_pos = fit_weekly_points_cv(conn)

    scenarios = sorted({c["scenario"] for c in cells})
    vintage_by_scenario = {
        s: build_data_vintage(conn, s, priors.latest_season) for s in scenarios
    }
    pool_by_scenario = {s: build_pool(conn, s) for s in scenarios}

    opponent_params = {
        "tau": TAU,
        "cand_window": CAND_WINDOW,
        "roster_damp": ROSTER_DAMP,
        "priors": priors.params,
    }
    sha = git_sha()

    t0 = datetime.datetime.now()
    for cell in cells:
        params = strategy_params_for_cell(cell)
        pool = pool_by_scenario[cell["scenario"]]
        result = run_cell(
            pool, priors, cv_by_pos, params, args.base_seed, cell["cell_idx"]
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
            f"scenario={cell['scenario']} qb_plan={cell['qb_plan_idx']} "
            f"defk={cell['defk_round']} tb={cell['tier_break_bonus']}: "
            f"all_play={result['all_play_pct']:.4f} se={result['all_play_se']:.4f} "
            f"top3={result['top3_rate']:.3f}"
        )

    elapsed = (datetime.datetime.now() - t0).total_seconds()
    print(f"farm run complete: {len(cells)} cells, {elapsed:.1f}s wall")


if __name__ == "__main__":
    main()
