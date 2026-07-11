"""Tests for scripts/run_sim_farm.py (grid construction, seed derivation,
staleness/mismatch refusal, cell persistence) and scripts/sim_report.py
(report rendering off seeded fixture rows). No full-farm run here (Task 12
brief) -- the heavy simulate-and-persist path is exercised with `run_cell`
monkeypatched to a canned result, mirroring `tests/test_run_backtests.py`'s
pattern for `run_all_cells`.
"""
import datetime
import sys

import pytest

from simfixtures import synthetic_priors

import run_sim_farm
import sim_report
from ffi.sim.calibrate import QbTimingMeasurement
from ffi.sim.strategy import StrategyParams

# Fixed sim-batch date for the render_report/main_for_date integration tests
# below. `_seed_batch` pins `sim.batches.started_at`/`finished_at` to this
# exact timestamp rather than letting the column fall back to its `now()`
# default -- `sim_report.load_batches` filters `started_at::date = %s`, so a
# fixture that relies on `now()` silently depends on the real wall-clock date
# matching the hardcoded query date, and breaks the instant a test run
# crosses a real calendar-day boundary (observed live: 2026-07-10 ->
# 2026-07-11 mid-session broke all 7 tests below). One source of truth here;
# change this constant, not the call sites.
BATCH_DATE = datetime.date(2026, 7, 10)
BATCH_STARTED_AT = datetime.datetime(2026, 7, 10, 12, 0, tzinfo=datetime.timezone.utc)

# ---------------------------------------------------------------------------
# Grid construction (72 cells, deterministic order, exact knob values)
# ---------------------------------------------------------------------------


def test_grid_has_72_cells():
    cells = run_sim_farm.build_grid()
    assert len(cells) == 72


def test_grid_cell_idx_is_sequential_from_zero():
    cells = run_sim_farm.build_grid()
    assert [c["cell_idx"] for c in cells] == list(range(72))


def test_grid_main_block_is_48_cells_qb_hoard_12_only():
    cells = run_sim_farm.build_grid()
    main_cells = [c for c in cells if c["grid"] == "main"]
    assert len(main_cells) == 48
    assert all(c["scenario"] == "qb_hoard_12" for c in main_cells)


def test_grid_subgrid_block_is_18_cells_defk_14_tb_0():
    cells = run_sim_farm.build_grid()
    sub_cells = [c for c in cells if c["grid"] == "qb_subgrid"]
    assert len(sub_cells) == 18
    assert all(c["defk_round"] == 14 for c in sub_cells)
    assert all(c["tier_break_bonus"] == 0.0 for c in sub_cells)
    assert {c["scenario"] for c in sub_cells} == {
        "qb_hoard_0",
        "qb_hoard_12",
        "qb_hoard_24",
    }


def test_grid_first_cell_matches_first_qb_plan_first_defk_first_tb():
    cells = run_sim_farm.build_grid()
    first = cells[0]
    assert first["qb_not_before"] == (1, 1, 1)
    assert first["qb_by_round"] == (1, 4, 9)
    assert first["defk_round"] == 8
    assert first["tier_break_bonus"] == 0.0
    assert first["scenario"] == "qb_hoard_12"


def test_grid_last_main_cell_is_last_qb_plan_last_defk_last_tb():
    cells = run_sim_farm.build_grid()
    last_main = cells[47]
    assert last_main["grid"] == "main"
    assert last_main["qb_not_before"] == (1, 4, 99)
    assert last_main["qb_by_round"] == (2, 7, 19)
    assert last_main["defk_round"] == 18
    assert last_main["tier_break_bonus"] == 8.0


def test_grid_main_cells_2_and_3_are_second_defk_round_value():
    # Closes a coverage gap: DEFK_ROUNDS' mid-range value (11, index 1 of
    # [8, 11, 14, 18]) is otherwise never pinned down by an exact-value
    # assertion (only the first (8) and last (18) values were).
    cells = run_sim_farm.build_grid()
    assert cells[2]["defk_round"] == run_sim_farm.DEFK_ROUNDS[1] == 11
    assert cells[2]["tier_break_bonus"] == 0.0
    assert cells[3]["defk_round"] == 11
    assert cells[3]["tier_break_bonus"] == 8.0


def test_grid_main_block_has_12_cells_per_defk_round():
    cells = run_sim_farm.build_grid()
    main_cells = [c for c in cells if c["grid"] == "main"]
    counts = {}
    for c in main_cells:
        counts[c["defk_round"]] = counts.get(c["defk_round"], 0) + 1
    assert counts == {8: 12, 11: 12, 14: 12, 18: 12}


def test_grid_last_subgrid_cell_is_last_qb_plan_last_subgrid_scenario():
    cells = run_sim_farm.build_grid()
    last_subgrid = cells[65]
    assert last_subgrid["grid"] == "qb_subgrid"
    assert last_subgrid["qb_not_before"] == (1, 4, 99)
    assert last_subgrid["qb_by_round"] == (2, 7, 19)
    assert last_subgrid["scenario"] == "qb_hoard_24"


def test_grid_qb_plan_count_matches_grid_constant():
    assert len(run_sim_farm.QB_PLANS) == 6
    assert len(run_sim_farm.DEFK_ROUNDS) == 4
    assert len(run_sim_farm.TIER_BREAK) == 2
    assert len(run_sim_farm.QB_TIER_PLANS) == 6


# ---------------------------------------------------------------------------
# qb_tier block (Phase 4 Task 6): 6 cells, idx 66-71, everything but
# qb_tier_targets fixed.
# ---------------------------------------------------------------------------


def test_grid_qb_tier_block_is_6_cells_idx_66_to_71():
    cells = run_sim_farm.build_grid()
    tier_cells = [c for c in cells if c["grid"] == "qb_tier"]
    assert len(tier_cells) == 6
    assert [c["cell_idx"] for c in tier_cells] == list(range(66, 72))


def test_grid_qb_tier_block_fixed_knobs():
    cells = run_sim_farm.build_grid()
    tier_cells = [c for c in cells if c["grid"] == "qb_tier"]
    assert all(c["scenario"] == "qb_hoard_12" for c in tier_cells)
    assert all(c["qb_not_before"] == (1, 1, 1) for c in tier_cells)
    assert all(c["qb_by_round"] == (2, 5, 9) for c in tier_cells)
    assert all(c["defk_round"] == 14 for c in tier_cells)
    assert all(c["tier_break_bonus"] == 0.0 for c in tier_cells)


def test_grid_qb_tier_block_targets_match_plans_in_order():
    cells = run_sim_farm.build_grid()
    tier_cells = [c for c in cells if c["grid"] == "qb_tier"]
    assert [c["qb_tier_targets"] for c in tier_cells] == run_sim_farm.QB_TIER_PLANS


def test_grid_qb_tier_first_cell_is_control_disabled():
    cells = run_sim_farm.build_grid()
    control = cells[66]
    assert control["qb_tier_targets"] == ()


def test_grid_last_cell_overall_is_last_qb_tier_plan():
    cells = run_sim_farm.build_grid()
    last = cells[71]
    assert last["grid"] == "qb_tier"
    assert last["qb_tier_targets"] == (2, 3, 3)


def test_strategy_params_for_cell_includes_qb_tier_targets():
    cells = run_sim_farm.build_grid()
    control = cells[66]
    params = run_sim_farm.strategy_params_for_cell(control)
    assert params.qb_tier_targets == ()

    aggressive = cells[71]
    params2 = run_sim_farm.strategy_params_for_cell(aggressive)
    assert params2.qb_tier_targets == (2, 3, 3)


def test_strategy_params_for_cell_main_grid_qb_tier_targets_defaults_empty():
    # main/qb_subgrid cells have no "qb_tier_targets" key -- must default to
    # () (inert), not KeyError.
    cells = run_sim_farm.build_grid()
    params = run_sim_farm.strategy_params_for_cell(cells[0])
    assert params.qb_tier_targets == ()


def test_grid_deterministic_across_calls():
    assert run_sim_farm.build_grid() == run_sim_farm.build_grid()


def test_cells_dev_cap_slices_grid():
    cells = run_sim_farm.build_grid()[:5]
    assert len(cells) == 5
    assert [c["cell_idx"] for c in cells] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------


def test_derive_seed_formula():
    assert run_sim_farm.derive_seed(20260710, 0, 0) == 20260710 * 100003
    assert run_sim_farm.derive_seed(20260710, 3, 7) == (
        20260710 * 100003 + 3 * 1009 + 7
    )


def test_derive_seed_unique_across_cells_and_drafts():
    seeds = {
        run_sim_farm.derive_seed(1, cell_idx, draft_idx)
        for cell_idx in range(72)
        for draft_idx in range(200)
    }
    assert len(seeds) == 72 * 200


# ---------------------------------------------------------------------------
# Cell strategy construction
# ---------------------------------------------------------------------------


def test_strategy_params_for_cell_builds_expected_params():
    cell = run_sim_farm.build_grid()[0]
    params = run_sim_farm.strategy_params_for_cell(cell)
    assert isinstance(params, StrategyParams)
    assert params.scenario == "qb_hoard_12"
    assert params.qb_by_round == (1, 4, 9)
    assert params.qb_not_before == (1, 1, 1)
    assert params.defk_round == 8
    assert params.tier_break_bonus == 0.0


# ---------------------------------------------------------------------------
# top3 rank helper
# ---------------------------------------------------------------------------


def test_is_top3_true_when_two_or_fewer_teams_beat_ours():
    pct_map = {1: 0.9, 2: 0.5, 3: 0.4, 4: 0.3}
    assert run_sim_farm.is_top3(pct_map, our_position=1) is True
    assert run_sim_farm.is_top3(pct_map, our_position=2) is True


def test_is_top3_false_when_three_or_more_teams_beat_ours():
    pct_map = {1: 0.9, 2: 0.8, 3: 0.7, 4: 0.3}
    assert run_sim_farm.is_top3(pct_map, our_position=4) is False


# ---------------------------------------------------------------------------
# Data-vintage refusal (ADR D2): stale snapshot / snapshot-vs-valuation mismatch
# ---------------------------------------------------------------------------


def _insert_xwalk_min(db, name, position, sleeper_id, gsis_id=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, position, sleeper_id, gsis_id)"
            " VALUES (%s,%s,%s,%s) RETURNING xwalk_id",
            (name, position, sleeper_id, gsis_id),
        )
        return cur.fetchone()[0]


def _insert_sleeper_snapshot(db, fetched_at):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.sleeper_projections (season, week, fetched_at, payload)"
            " VALUES (2026, NULL, %s, '[]'::jsonb) RETURNING snapshot_id",
            (fetched_at,),
        )
        return cur.fetchone()[0]


def _insert_player_value(db, xwalk_id, scenario, snapshot_id, config_version=1):
    from ffi.scoring.config import ensure_config_in_db, load_config_v1

    ensure_config_in_db(db, load_config_v1())
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO valuation.player_value
               (config_version, scenario, xwalk_id, position, proj_points, vorp, tier, params)
               VALUES (%s,%s,%s,'QB',100.0,10.0,1,%s)""",
            (config_version, scenario, xwalk_id, f'{{"snapshot_id": {snapshot_id}}}'),
        )
    db.commit()


def test_build_data_vintage_passes_when_fresh_and_matched(db, monkeypatch):
    monkeypatch.setattr(
        run_sim_farm, "load_config_v1", lambda: type("C", (), {"version": 1})()
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    snap_id = _insert_sleeper_snapshot(db, now)
    xid = _insert_xwalk_min(db, "Test QB", "QB", "sleeper1")
    _insert_player_value(db, xid, "qb_hoard_12", snap_id)

    vintage = run_sim_farm.build_data_vintage(
        db, "qb_hoard_12", priors_latest_season=2025
    )
    assert vintage["adp_snapshot_id"] == snap_id
    assert vintage["valuation_snapshot_id"] == snap_id
    assert vintage["priors_latest_season"] == 2025
    assert vintage["degraded"] is False


def test_build_data_vintage_refuses_when_stale(db, monkeypatch):
    monkeypatch.setattr(
        run_sim_farm, "load_config_v1", lambda: type("C", (), {"version": 1})()
    )
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=40)
    snap_id = _insert_sleeper_snapshot(db, old)
    xid = _insert_xwalk_min(db, "Test QB", "QB", "sleeper1")
    _insert_player_value(db, xid, "qb_hoard_12", snap_id)

    with pytest.raises(SystemExit, match="40"):
        run_sim_farm.build_data_vintage(db, "qb_hoard_12", priors_latest_season=2025)


def test_build_data_vintage_refuses_on_snapshot_mismatch(db, monkeypatch):
    monkeypatch.setattr(
        run_sim_farm, "load_config_v1", lambda: type("C", (), {"version": 1})()
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    old_snap_id = _insert_sleeper_snapshot(db, now - datetime.timedelta(hours=10))
    new_snap_id = _insert_sleeper_snapshot(db, now)
    xid = _insert_xwalk_min(db, "Test QB", "QB", "sleeper1")
    _insert_player_value(
        db, xid, "qb_hoard_12", old_snap_id
    )  # stale valuation vs latest ADP

    with pytest.raises(SystemExit, match="mismatch|snapshot"):
        run_sim_farm.build_data_vintage(db, "qb_hoard_12", priors_latest_season=2025)


def test_build_data_vintage_refuses_when_no_valuation_rows(db, monkeypatch):
    monkeypatch.setattr(
        run_sim_farm, "load_config_v1", lambda: type("C", (), {"version": 1})()
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    _insert_sleeper_snapshot(db, now)

    with pytest.raises(SystemExit, match="build_valuation"):
        run_sim_farm.build_data_vintage(db, "qb_hoard_12", priors_latest_season=2025)


# ---------------------------------------------------------------------------
# Cell persistence (run_cell stubbed with a canned result, like test_run_backtests.py)
# ---------------------------------------------------------------------------


def _canned_cell_result():
    picks = [
        {
            "overall": i + 1,
            "position_slot": (i % 12) + 1,
            "franchise_slot": (i % 12) + 1,
            "pos": "QB" if i < 12 else "RB",
            "ref": f"r{i}",
            "name": f"Player {i}",
        }
        for i in range(24)
    ]
    roster = [
        {
            "ref": "r0",
            "name": "Player 0",
            "position": "QB",
            "proj_points": 300.0,
            "vorp": 50.0,
            "tier": 1,
            "adp": 5.0,
        }
    ]
    sample = {
        "seed": 12345,
        "all_play_pct": 0.55,
        "our_position": 1,
        "picks": picks,
        "our_roster": roster,
    }
    return {
        "all_play_pct": 0.55,
        "all_play_se": 0.01,
        "top3_rate": 0.4,
        "qb1_round_mean": 1.2,
        "def_round_mean": 14.0,
        "n_drafts": 200,
        "samples": {"worst": sample, "best": sample, "random": sample},
    }


class _DummyPriors:
    latest_season = 2025
    params = {"half_life": 4.0}


class _DummyCfg:
    version = 1


def _patch_common(monkeypatch, db):
    monkeypatch.setattr(run_sim_farm, "connect", lambda: db)
    monkeypatch.setattr(run_sim_farm, "load_config_v1", lambda: _DummyCfg())
    monkeypatch.setattr(run_sim_farm, "build_slot_priors", lambda conn: _DummyPriors())
    monkeypatch.setattr(run_sim_farm, "fit_weekly_points_cv", lambda conn: {})
    monkeypatch.setattr(run_sim_farm, "build_pool", lambda conn, scenario: [])
    monkeypatch.setattr(
        run_sim_farm,
        "build_data_vintage",
        lambda conn, scenario, priors_latest_season: {
            "adp_snapshot_id": 1,
            "adp_snapshot_fetched_at": "2026-07-10T00:00:00+00:00",
            "adp_age_hours": 1.0,
            "valuation_snapshot_id": 1,
            "valuation_computed_at": "2026-07-10T00:00:00+00:00",
            "priors_latest_season": priors_latest_season,
            "degraded": False,
        },
    )
    monkeypatch.setattr(run_sim_farm, "run_cell", lambda *a, **k: _canned_cell_result())
    monkeypatch.setattr(run_sim_farm, "git_sha", lambda: "deadbeef")


def _counts(db):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM sim.batches")
        n_batches = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sim.batch_results")
        n_results = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sim.sample_drafts")
        n_samples = cur.fetchone()[0]
    return n_batches, n_results, n_samples


def test_main_persists_one_batch_per_cell_with_cells_cap(monkeypatch, db):
    _patch_common(monkeypatch, db)
    monkeypatch.setattr(
        sys, "argv", ["run_sim_farm.py", "--base-seed", "20260710", "--cells", "3"]
    )
    run_sim_farm.main()

    n_batches, n_results, n_samples = _counts(db)
    assert n_batches == 3
    assert n_results == 3 * 5  # 5 metrics per batch
    assert n_samples == 3 * 3  # worst/best/random per batch

    with db.cursor() as cur:
        cur.execute(
            "SELECT kind, scenario, base_seed, data_vintage FROM sim.batches LIMIT 1"
        )
        kind, scenario, base_seed, data_vintage = cur.fetchone()
        assert kind == "farm"
        assert scenario == "qb_hoard_12"
        assert base_seed == 20260710
        assert data_vintage["priors_latest_season"] == 2025


def test_main_requires_base_seed(monkeypatch, db):
    _patch_common(monkeypatch, db)
    monkeypatch.setattr(sys, "argv", ["run_sim_farm.py"])
    with pytest.raises(SystemExit):
        run_sim_farm.main()


# ---------------------------------------------------------------------------
# git_sha: dirty-aware, no fallback (Phase 3 Minor)
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_git_run(cmd, dirty_status):
    if cmd[:2] == ["git", "rev-parse"]:
        return _FakeCompletedProcess("abc1234\n")
    if cmd[:2] == ["git", "status"]:
        return _FakeCompletedProcess(dirty_status)
    raise AssertionError(f"unexpected command {cmd}")


def test_git_sha_appends_dirty_suffix_when_tree_is_dirty(monkeypatch):
    monkeypatch.setattr(
        run_sim_farm.subprocess,
        "run",
        lambda cmd, **kwargs: _fake_git_run(cmd, " M scripts/run_sim_farm.py\n"),
    )
    assert run_sim_farm.git_sha() == "abc1234-dirty"


def test_git_sha_no_suffix_when_tree_is_clean(monkeypatch):
    monkeypatch.setattr(
        run_sim_farm.subprocess,
        "run",
        lambda cmd, **kwargs: _fake_git_run(cmd, ""),
    )
    assert run_sim_farm.git_sha() == "abc1234"


# ---------------------------------------------------------------------------
# sim_report.py: render_report on seeded fixture batches
# ---------------------------------------------------------------------------


def _build_picks_with_qb1_rounds(qb1_round_by_team: dict, n_rounds: int = 6) -> list:
    """228-pick-shaped (well, n_rounds*12) log where team `pos_slot`'s FIRST
    QB pick lands at round `qb1_round_by_team[pos_slot]` (every other pick is
    a filler RB) -- lets a test control exactly what the league-wide QB1
    audit sees, independent of the batch's own (our-seat-only) qb1_round_mean
    metric."""
    picks = []
    overall = 0
    for rnd in range(1, n_rounds + 1):
        order = range(1, 13) if rnd % 2 == 1 else range(12, 0, -1)
        for pos_slot in order:
            overall += 1
            is_qb = qb1_round_by_team.get(pos_slot) == rnd
            picks.append(
                {
                    "overall": overall,
                    "position_slot": pos_slot,
                    "franchise_slot": pos_slot,
                    "pos": "QB" if is_qb else "RB",
                    "ref": f"r{overall}",
                    "name": f"P{overall}",
                }
            )
    return picks


def _seed_batch(
    db,
    scenario,
    qb_plan_idx,
    defk_round,
    tier_break,
    cell_idx,
    all_play_pct,
    qb1_round_mean=2.0,
    def_round_mean=14.0,
    degraded=False,
    top3_rate=0.25,
    grid="main",
    qb1_round_by_team=None,
):
    strategy = {
        "scenario": scenario,
        "qb_by_round": [1, 4, 9],
        "qb_not_before": [1, 1, 1],
        "defk_round": defk_round,
        "caps": [["QB", 4], ["RB", 9], ["WR", 9], ["TE", 3], ["K", 2], ["DEF", 2]],
        "tier_break_bonus": tier_break,
        "cell_idx": cell_idx,
        "grid": grid,
        "qb_plan_idx": qb_plan_idx,
    }
    data_vintage = {
        "adp_snapshot_id": 42,
        "adp_snapshot_fetched_at": "2026-07-10T00:00:00+00:00",
        "adp_age_hours": 5.0,
        "valuation_snapshot_id": 42,
        "valuation_computed_at": "2026-07-10T00:00:00+00:00",
        "priors_latest_season": 2025,
        "degraded": degraded,
    }
    import json as _json

    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO sim.batches
               (kind, git_sha, config_version, scenario, season, strategy,
                opponent_params, n_drafts, seasons_per_draft, base_seed, data_vintage,
                started_at, finished_at)
               VALUES ('farm','deadbeef',1,%s,NULL,%s,'{}'::jsonb,200,20,20260710,%s,%s,%s)
               RETURNING batch_id""",
            (
                scenario,
                _json.dumps(strategy),
                _json.dumps(data_vintage),
                BATCH_STARTED_AT,
                BATCH_STARTED_AT,
            ),
        )
        batch_id = cur.fetchone()[0]
        for metric, val in (
            ("all_play_pct", all_play_pct),
            ("all_play_se", 0.01),
            ("top3_rate", top3_rate),
            ("qb1_round_mean", qb1_round_mean),
            ("def_round_mean", def_round_mean),
        ):
            cur.execute(
                "INSERT INTO sim.batch_results (batch_id, metric, value) VALUES (%s,%s,%s)",
                (batch_id, metric, val),
            )
        if qb1_round_by_team is None:
            qb1_round_by_team = {slot: 1 for slot in range(1, 13)}
        picks = _build_picks_with_qb1_rounds(qb1_round_by_team)
        roster = [
            {
                "ref": "r0",
                "name": "Worst QB",
                "position": "QB",
                "proj_points": 250.0,
                "vorp": 10.0,
                "tier": 2,
                "adp": 20.0,
            }
        ]
        for reason, pct in (
            ("worst", all_play_pct - 0.05),
            ("best", all_play_pct + 0.05),
            ("random", all_play_pct),
        ):
            cur.execute(
                """INSERT INTO sim.sample_drafts
                   (batch_id, draft_seed, reason, our_position, all_play_pct, picks, our_roster)
                   VALUES (%s,%s,%s,1,%s,%s,%s)""",
                (
                    batch_id,
                    hash(reason) % 100000,
                    reason,
                    pct,
                    _json.dumps(picks),
                    _json.dumps(roster),
                ),
            )
    db.commit()
    return batch_id


@pytest.fixture
def stub_audit(monkeypatch):
    """The Task 4 assumption audit builds the live pool/priors/history off the
    connection and draws a uniform opponent sample -- data the seeded test DB
    doesn't carry. The render_report integration tests below assert on OTHER
    sections (vintage/qb-policy/defk/worst), so they stub the audit to a fixed
    (lines, ok=True) pair; the audit's own logic is unit-tested separately in
    `test_assumption_audit_lines_*`."""
    monkeypatch.setattr(
        sim_report,
        "_run_assumption_audit",
        lambda conn, date: (["## Assumption audit", ""], True),
    )


def test_render_report_includes_data_vintage_header(db, stub_audit):
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=0,
        all_play_pct=0.55,
    )
    report, _ok = sim_report.render_report(db, BATCH_DATE)
    assert "data vintage" in report.lower() or "data-vintage" in report.lower()
    assert "42" in report  # snapshot id
    assert "deadbeef" in report  # git sha


def test_render_report_includes_qb_policy_table(db, stub_audit):
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=48,
        all_play_pct=0.55,
        grid="qb_subgrid",
    )
    _seed_batch(
        db,
        "qb_hoard_0",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=49,
        all_play_pct=0.50,
        grid="qb_subgrid",
    )
    report, _ok = sim_report.render_report(db, BATCH_DATE)
    assert "QB" in report
    assert "0.55" in report or "55.0" in report


def test_render_report_defk_table_by_round(db, stub_audit):
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=8,
        tier_break=0.0,
        cell_idx=0,
        all_play_pct=0.50,
    )
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=18,
        tier_break=0.0,
        cell_idx=1,
        all_play_pct=0.53,
    )
    report, _ok = sim_report.render_report(db, BATCH_DATE)
    assert "defk_round" in report.lower() or "def/k" in report.lower()


def test_render_report_worst_drafts_narrative_present(db, stub_audit):
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=0,
        all_play_pct=0.30,
    )
    report, _ok = sim_report.render_report(db, BATCH_DATE)
    assert "worst" in report.lower()


# --- Task 4: uniform-sample assumption audit (pure logic, no DB) --------------

_AUDIT_PRIORS = synthetic_priors()


def _audit_measurement(qb1_mean: float):
    """A minimal QbTimingMeasurement carrying a chosen opponent QB1-round mean
    and a QB-heavy R1-3 pos-share (so the deviations table renders a row)."""
    return QbTimingMeasurement(
        n_drafts=100,
        league_means=(qb1_mean, 5.0, 9.0),
        per_slot={},
        pos_share_by_band={("R1-3", "QB"): 0.5, ("R4-8", "RB"): 0.3},
    )


# historical seasons-weighted QB1 mean = 2.0 here (single slot, seasons-weighted).
_AUDIT_HIST = {1: {"qb1": 2.0, "qb2": 5.0, "qb3": 10.0, "seasons": 16.0}}


def test_assumption_audit_lines_ok_within_tolerance():
    lines, ok = sim_report._assumption_audit_lines(
        _audit_measurement(2.3), _AUDIT_PRIORS, _AUDIT_HIST
    )
    assert ok is True
    text = "\n".join(lines)
    assert "within" in text
    assert "REGRESSION" not in text


def test_assumption_audit_lines_regression_outside_tolerance():
    # QB1 mean 3.0 vs historical 2.0 -> diff 1.0 > 0.5 tolerance.
    lines, ok = sim_report._assumption_audit_lines(
        _audit_measurement(3.0), _AUDIT_PRIORS, _AUDIT_HIST
    )
    assert ok is False
    assert "REGRESSION" in "\n".join(lines)


def test_assumption_audit_pos_share_table_uses_band_averaged_priors():
    lines, _ok = sim_report._assumption_audit_lines(
        _audit_measurement(2.0), _AUDIT_PRIORS, _AUDIT_HIST
    )
    text = "\n".join(lines)
    assert "priors share" in text  # table compares sim vs priors, not uniform
    assert "| R1-3 | QB |" in text


def test_render_report_exits_nonzero_when_batch_degraded(
    db, tmp_path, monkeypatch, stub_audit
):
    # `main_for_date` writes reports/sim-farm-<date>.md to disk. Never let a
    # test touch the real repo `reports/` dir -- redirect REPORTS_DIR to an
    # ephemeral tmp_path so this run can't clobber a real generated report
    # (see Task 12 review finding: tests were overwriting the on-disk
    # sim-farm-2026-07-10.md with fixture data, e.g. snapshot #42/deadbeef).
    monkeypatch.setattr(sim_report, "REPORTS_DIR", tmp_path)
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=0,
        all_play_pct=0.50,
        degraded=True,
    )
    with pytest.raises(SystemExit):
        sim_report.main_for_date(db, BATCH_DATE)


def test_render_report_exits_zero_when_no_batch_degraded(
    db, tmp_path, monkeypatch, stub_audit
):
    # See comment in test_render_report_exits_nonzero_when_batch_degraded above:
    # redirect REPORTS_DIR so this test can't overwrite the real report file.
    monkeypatch.setattr(sim_report, "REPORTS_DIR", tmp_path)
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=0,
        all_play_pct=0.50,
        degraded=False,
    )
    # Should not raise.
    out = sim_report.main_for_date(db, BATCH_DATE)
    assert out.parent == tmp_path


def test_main_for_date_exits_nonzero_on_audit_regression(db, tmp_path, monkeypatch):
    # The audit is now a HARD regression check: a failing QB1 audit must make
    # main_for_date exit nonzero even when no batch is degraded. Stub the audit
    # to fail; the report is still written (evidence) before the exit.
    monkeypatch.setattr(sim_report, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        sim_report,
        "_run_assumption_audit",
        lambda conn, date: (["## Assumption audit", "- REGRESSION: ..."], False),
    )
    _seed_batch(
        db,
        "qb_hoard_12",
        qb_plan_idx=0,
        defk_round=14,
        tier_break=0.0,
        cell_idx=0,
        all_play_pct=0.50,
        degraded=False,
    )
    with pytest.raises(SystemExit, match="regression"):
        sim_report.main_for_date(db, BATCH_DATE)
    # report written before the exit
    assert (tmp_path / f"sim-farm-{BATCH_DATE.isoformat()}.md").exists()


def test_render_report_raises_when_no_batches_for_date(db):
    with pytest.raises(ValueError, match="no.*batch"):
        sim_report.render_report(db, datetime.date(2099, 1, 1))
