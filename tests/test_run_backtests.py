"""Tests for scripts/run_backtests.py's --reference vs --gate persistence
split (Task 11 review finding): --gate must be read-only against
sim.batches/batch_results -- only --reference persists audit rows.

The heavy compute (`run_all_cells`, `build_slot_priors`, `season_data_vintage`)
is stubbed so these tests don't depend on populated `sim.backtest_pool` data;
they exercise `main()`'s control flow and the DB side effects directly.
"""
import sys

import run_backtests


def _canned_results():
    return [
        {
            "strategy_idx": 0,
            "season": 2023,
            "all_play_pct": 0.50,
            "all_play_se": 0.01,
            "qb1_round_mean": 2.0,
            "n_drafts": 100,
        },
        {
            "strategy_idx": 1,
            "season": 2023,
            "all_play_pct": 0.52,
            "all_play_se": 0.01,
            "qb1_round_mean": 3.0,
            "n_drafts": 100,
        },
    ]


class _DummyPriors:
    params = {}


class _DummyCfg:
    version = 1


def _patch_common(monkeypatch, db):
    monkeypatch.setattr(run_backtests, "connect", lambda: db)
    monkeypatch.setattr(run_backtests, "load_config_v1", lambda: _DummyCfg())
    monkeypatch.setattr(run_backtests, "run_all_cells", lambda conn: _canned_results())
    monkeypatch.setattr(run_backtests, "build_slot_priors", lambda conn: _DummyPriors())
    monkeypatch.setattr(
        run_backtests,
        "season_data_vintage",
        lambda conn, season: {"season": season, "degraded_by_position": {}},
    )


def _batch_counts(db):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM sim.batches")
        n_batches = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sim.batch_results")
        n_results = cur.fetchone()[0]
    return n_batches, n_results


def _seed_active_reference(db, composite=0.40, band=0.20):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO sim.backtest_reference
               (git_sha, description, composite, band, detail, is_active)
               VALUES ('deadbeef','seed for gate test',%s,%s,'[]'::jsonb,true)""",
            (composite, band),
        )
    db.commit()


def test_gate_run_does_not_persist_batches(monkeypatch, db):
    _patch_common(monkeypatch, db)
    _seed_active_reference(db)  # composite 0.51 (canned) must clear 0.40-0.20=0.20

    monkeypatch.setattr(sys, "argv", ["run_backtests.py", "--gate"])
    run_backtests.main()

    assert _batch_counts(db) == (0, 0)


def test_reference_run_persists_batches(monkeypatch, db):
    _patch_common(monkeypatch, db)

    monkeypatch.setattr(sys, "argv", ["run_backtests.py", "--reference"])
    run_backtests.main()

    n_batches, n_results = _batch_counts(db)
    assert n_batches == len(_canned_results())
    # 3 metrics persisted per batch (all_play_pct, all_play_se, qb1_round_mean)
    assert n_results == 3 * len(_canned_results())


def test_reference_run_activates_new_reference_row(monkeypatch, db):
    _patch_common(monkeypatch, db)
    _seed_active_reference(db)  # a prior active row that --reference must deactivate

    monkeypatch.setattr(sys, "argv", ["run_backtests.py", "--reference"])
    run_backtests.main()

    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM sim.backtest_reference WHERE is_active=true")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT composite FROM sim.backtest_reference WHERE is_active=true")
        (composite,) = cur.fetchone()
        assert float(composite) == 0.51  # mean(0.50, 0.52) from _canned_results
