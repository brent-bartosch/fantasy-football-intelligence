"""Tests for scripts/run_sim_farm.py (grid construction, seed derivation,
staleness/mismatch refusal, cell persistence) and scripts/sim_report.py
(report rendering off seeded fixture rows). No full-farm run here (Task 12
brief) -- the heavy simulate-and-persist path is exercised with `run_cell`
monkeypatched to a canned result, mirroring `tests/test_run_backtests.py`'s
pattern for `run_all_cells`.

v2 deploy re-center (2026-07-21): the farm is now a 15-cell A' sensitivity grid
(qb_by_round x defk_round) anchored on DEPLOYED_PARAMS, and every cell records
`playoff_make_pct` (H2H) alongside all_play. The old 72-cell QB_PLANS/qb_subgrid/
qb_tier/tier_break design is retired; these tests track the new grid.
"""
import datetime
import sys
from collections import Counter

import pytest

from simfixtures import synthetic_priors

import run_sim_farm
import sim_report
from ffi.sim.calibrate import QbTimingMeasurement
from ffi.sim.strategy import DEPLOYED_PARAMS

# Fixed sim-batch date for the render_report/main_for_date integration tests
# below (see original note: pins started_at so a calendar-day boundary can't
# break the fixture). One source of truth; change here, not the call sites.
BATCH_DATE = datetime.date(2026, 7, 10)
BATCH_STARTED_AT = datetime.datetime(2026, 7, 10, 12, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Grid construction (15-cell A' sensitivity grid, anchor-first)
# ---------------------------------------------------------------------------


def test_grid_has_15_cells():
    assert len(run_sim_farm.build_grid()) == 15


def test_grid_cell_idx_is_sequential_from_zero():
    cells = run_sim_farm.build_grid()
    assert [c["cell_idx"] for c in cells] == list(range(15))


def test_grid_cell0_is_anchor_deployed():
    a = run_sim_farm.build_grid()[0]
    assert a["grid"] == "anchor"
    assert a["qb_by_round"] == run_sim_farm.ANCHOR_QB_BY_ROUND == (2, 5, 14)
    assert a["defk_round"] == run_sim_farm.ANCHOR_DEFK == 14
    assert a["scenario"] == "qb_hoard_12"


def test_grid_all_cells_scenario_qb_hoard_12():
    assert all(c["scenario"] == "qb_hoard_12" for c in run_sim_farm.build_grid())


def test_grid_axis_labels_and_counts():
    counts = Counter(c["grid"] for c in run_sim_farm.build_grid())
    # 1 anchor + 4 qb_timing (5 plans - anchor) + 2 defk (3 rounds - anchor)
    # + 8 cross (4 off-anchor qb x 2 off-anchor defk)
    assert counts == {"anchor": 1, "qb_timing": 4, "defk": 2, "cross": 8}


def test_grid_qb_timing_cells_hold_defk_at_anchor():
    for c in run_sim_farm.build_grid():
        if c["grid"] == "qb_timing":
            assert c["defk_round"] == run_sim_farm.ANCHOR_DEFK
            assert c["qb_by_round"] != run_sim_farm.ANCHOR_QB_BY_ROUND


def test_grid_defk_cells_hold_qb_at_anchor():
    for c in run_sim_farm.build_grid():
        if c["grid"] == "defk":
            assert c["qb_by_round"] == run_sim_farm.ANCHOR_QB_BY_ROUND
            assert c["defk_round"] != run_sim_farm.ANCHOR_DEFK


def test_grid_constants():
    assert len(run_sim_farm.QB_BY_ROUND_PLANS) == 5
    assert run_sim_farm.QB_BY_ROUND_PLANS[0] == (2, 5, 14)
    assert len(run_sim_farm.DEFK_ROUNDS) == 3
    assert 14 in run_sim_farm.DEFK_ROUNDS


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
        for cell_idx in range(15)
        for draft_idx in range(200)
    }
    assert len(seeds) == 15 * 200


# ---------------------------------------------------------------------------
# Cell strategy construction (every cell is A' = DEPLOYED with 2 knobs varied)
# ---------------------------------------------------------------------------


def test_strategy_params_for_cell_anchor_equals_deployed():
    params = run_sim_farm.strategy_params_for_cell(run_sim_farm.build_grid()[0])
    assert params == DEPLOYED_PARAMS


def test_strategy_params_for_cell_is_pstart_mode_with_overrides():
    for cell in run_sim_farm.build_grid():
        p = run_sim_farm.strategy_params_for_cell(cell)
        assert p.pstart_weights == DEPLOYED_PARAMS.pstart_weights  # A' engine
        assert p.pstart_weights != ()  # not legacy vorp mode
        assert p.qb_by_round == cell["qb_by_round"]
        assert p.defk_round == cell["defk_round"]
        assert p.scenario == "qb_hoard_12"


# ---------------------------------------------------------------------------
# H2H playoff-make evaluator + top3 helper
# ---------------------------------------------------------------------------


def test_round_robin_is_11_rounds_of_6():
    rr = run_sim_farm._round_robin(12)
    assert len(rr) == 11
    assert all(len(week) == 6 for week in rr)
    # every team plays every other exactly once across the schedule
    pairs = {tuple(sorted(m)) for week in rr for m in week}
    assert len(pairs) == 66  # C(12, 2)


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
        "playoff_make_pct": 0.90,
        "playoff_make_se": 0.02,
        "top3_rate": 0.4,
        "qb1_round_mean": 1.2,
        "def_round_mean": 14.0,
        "n_drafts": 200,
        "n_seasons": 20,
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
    assert n_results == 3 * 7  # 7 metrics per batch (incl. playoff_make_pct/se)
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
        # playoff-make metric is recorded
        cur.execute(
            "SELECT count(*) FROM sim.batch_results WHERE metric='playoff_make_pct'"
        )
        assert cur.fetchone()[0] == 3


def test_smoke_flag_reduces_grid(monkeypatch, db):
    _patch_common(monkeypatch, db)
    monkeypatch.setattr(
        sys, "argv", ["run_sim_farm.py", "--base-seed", "20260710", "--smoke"]
    )
    run_sim_farm.main()
    n_batches, _, _ = _counts(db)
    assert n_batches == 3  # --smoke caps to 3 cells


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
    """n_rounds*12-pick log where team `pos_slot`'s FIRST QB pick lands at round
    `qb1_round_by_team[pos_slot]` (every other pick is a filler RB)."""
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
    cell_idx,
    all_play_pct,
    qb_by_round=(2, 5, 14),
    defk_round=14,
    grid="anchor",
    playoff_make_pct=0.90,
    qb1_round_mean=2.0,
    def_round_mean=14.0,
    degraded=False,
    top3_rate=0.25,
    qb1_round_by_team=None,
):
    strategy = {
        "scenario": scenario,
        "qb_by_round": list(qb_by_round),
        "qb_not_before": [1, 1, 1],
        "defk_round": defk_round,
        "caps": [["QB", 4], ["RB", 9], ["WR", 9], ["TE", 2], ["K", 1], ["DEF", 1]],
        "tier_break_bonus": 0.0,
        "cell_idx": cell_idx,
        "grid": grid,
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
            ("playoff_make_pct", playoff_make_pct),
            ("playoff_make_se", 0.015),
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
    """Stub the live-DB assumption audit to a fixed (lines, ok=True) pair; its
    own logic is unit-tested separately in `test_assumption_audit_lines_*`."""
    monkeypatch.setattr(
        sim_report,
        "_run_assumption_audit",
        lambda conn, date: (["## Assumption audit", ""], True),
    )


def test_render_report_includes_data_vintage_header(db, stub_audit):
    _seed_batch(db, "qb_hoard_12", cell_idx=0, all_play_pct=0.55)
    report, _ok = sim_report.render_report(db, BATCH_DATE)
    assert "data vintage" in report.lower() or "data-vintage" in report.lower()
    assert "42" in report  # snapshot id
    assert "deadbeef" in report  # git sha


def test_render_report_strategy_grid_surfaces_playoff_make(db, stub_audit):
    # anchor + one qb_timing cell -> the grid table shows playoff-make% and a
    # delta-vs-anchor column.
    _seed_batch(
        db,
        "qb_hoard_12",
        cell_idx=0,
        all_play_pct=0.73,
        grid="anchor",
        qb_by_round=(2, 5, 14),
        playoff_make_pct=0.98,
    )
    _seed_batch(
        db,
        "qb_hoard_12",
        cell_idx=1,
        all_play_pct=0.67,
        grid="qb_timing",
        qb_by_round=(1, 4, 9),
        playoff_make_pct=0.95,
    )
    report, _ok = sim_report.render_report(db, BATCH_DATE)
    assert "sensitivity grid" in report.lower()
    assert "playoff-make" in report.lower()
    assert "anchor" in report.lower()
    assert "vs anchor" in report.lower()  # delta column present


def test_render_report_worst_drafts_narrative_present(db, stub_audit):
    _seed_batch(db, "qb_hoard_12", cell_idx=0, all_play_pct=0.30)
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
    monkeypatch.setattr(sim_report, "REPORTS_DIR", tmp_path)
    _seed_batch(db, "qb_hoard_12", cell_idx=0, all_play_pct=0.50, degraded=True)
    with pytest.raises(SystemExit):
        sim_report.main_for_date(db, BATCH_DATE)


def test_render_report_exits_zero_when_no_batch_degraded(
    db, tmp_path, monkeypatch, stub_audit
):
    monkeypatch.setattr(sim_report, "REPORTS_DIR", tmp_path)
    _seed_batch(db, "qb_hoard_12", cell_idx=0, all_play_pct=0.50, degraded=False)
    out = sim_report.main_for_date(db, BATCH_DATE)
    assert out.parent == tmp_path


def test_main_for_date_exits_nonzero_on_audit_regression(db, tmp_path, monkeypatch):
    monkeypatch.setattr(sim_report, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        sim_report,
        "_run_assumption_audit",
        lambda conn, date: (["## Assumption audit", "- REGRESSION: ..."], False),
    )
    _seed_batch(db, "qb_hoard_12", cell_idx=0, all_play_pct=0.50, degraded=False)
    with pytest.raises(SystemExit, match="regression"):
        sim_report.main_for_date(db, BATCH_DATE)
    assert (tmp_path / f"sim-farm-{BATCH_DATE.isoformat()}.md").exists()


def test_render_report_raises_when_no_batches_for_date(db):
    with pytest.raises(ValueError, match="no.*batch"):
        sim_report.render_report(db, datetime.date(2099, 1, 1))
