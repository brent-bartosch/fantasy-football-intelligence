import pytest

from ffi.usage import METRICS, UsageFrame, UsageRow
from ffi.usage.build import build_usage_weekly, load_usage_weekly, store_usage_weekly


def _seed(db, rows, snaps=()):
    """rows: (gsis_id, team, position, targets, carries)
    snaps: (gsis_id, team, position, offense_pct)"""
    with db.cursor() as cur:
        for gsis_id, team, position, targets, carries in rows:
            cur.execute(
                "INSERT INTO raw.nflverse_player_week "
                "(gsis_id, season, week, team, position, targets, carries) "
                "VALUES (%s, 2025, 3, %s, %s, %s, %s)",
                (gsis_id, team, position, targets, carries),
            )
        for gsis_id, team, position, pct in snaps:
            cur.execute(
                "INSERT INTO raw.nflverse_snap_counts "
                "(gsis_id, season, week, team, position, offense_snaps, offense_pct) "
                "VALUES (%s, 2025, 3, %s, %s, %s, %s)",
                (gsis_id, team, position, pct * 70, pct),
            )
    db.commit()


# A complete team-week: 34 targets + 26 carries = 60 plays, over the floor.
COMPLETE_SF = [
    ("00-0000001", "SF", "WR", 12, 0),
    ("00-0000002", "SF", "WR", 10, 0),
    ("00-0000003", "SF", "TE", 8, 0),
    ("00-0000004", "SF", "RB", 4, 20),
    ("00-0000005", "SF", "QB", 0, 6),
]


def test_shares_computed_on_a_complete_team_week(db):
    _seed(db, COMPLETE_SF, snaps=[("00-0000001", "SF", "WR", 0.82)])
    frame = build_usage_weekly(db, 2025, 3)
    by_id = {r.gsis_id: r for r in frame.rows}
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34)
    assert by_id["00-0000004"].carry_share == pytest.approx(20 / 26)
    assert by_id["00-0000001"].snap_share == pytest.approx(0.82)
    assert all(r.games_complete == 1 for r in frame.rows)


def test_partial_team_week_refuses_to_compute_shares(db):
    # 6 targets + 3 carries = 9 plays: nflverse is mid-publish, not the 49ers
    # having run nine plays. A short denominator here would put one WR at
    # 100% target share and fire a false ASCENDING for the whole team.
    _seed(db, [("00-0000009", "SF", "WR", 6, 3)])
    frame = build_usage_weekly(db, 2025, 3)
    row = frame.rows[0]
    assert row.games_complete == 0
    assert row.target_share is None
    assert row.carry_share is None
    # The raw counts are still recorded — the refusal must be auditable.
    assert (row.team_targets, row.team_carries) == (6, 3)


def test_snap_share_is_kept_on_a_partial_week(db):
    # snap_share is published by nflverse as a share, not derived from a
    # denominator we compute, so the partial-publish guard does not apply.
    _seed(
        db, [("00-0000009", "SF", "WR", 6, 3)], snaps=[("00-0000009", "SF", "WR", 0.9)]
    )
    row = build_usage_weekly(db, 2025, 3).rows[0]
    assert row.games_complete == 0
    assert row.snap_share == pytest.approx(0.9)


def test_unavailable_metrics_are_null_and_announced(db):
    _seed(db, COMPLETE_SF)
    frame = build_usage_weekly(db, 2025, 3)
    assert frame.disabled_metrics == ("route_share", "rz_touches")
    assert frame.available_metrics == frozenset(
        {"snap_share", "target_share", "carry_share"}
    )
    assert all(r.route_share is None and r.rz_touches is None for r in frame.rows)
    assert set(METRICS) == frame.available_metrics | set(frame.disabled_metrics)


def test_teams_observed_counts_distinct_teams(db):
    _seed(db, COMPLETE_SF + [("00-0000010", "SEA", "WR", 30, 25)])
    frame = build_usage_weekly(db, 2025, 3)
    assert frame.teams_observed == 2
    assert all(r.teams_observed == 2 for r in frame.rows)


def test_empty_week_raises_rather_than_returning_an_empty_frame(db):
    with pytest.raises(ValueError, match="no rows"):
        build_usage_weekly(db, 2025, 3)


def test_store_and_load_round_trip(db):
    _seed(db, COMPLETE_SF, snaps=[("00-0000001", "SF", "WR", 0.82)])
    frame = build_usage_weekly(db, 2025, 3)
    assert store_usage_weekly(db, frame) == 5
    loaded = load_usage_weekly(db, 2025, 3)
    assert {r.gsis_id for r in loaded.rows} == {r.gsis_id for r in frame.rows}
    assert loaded.available_metrics == frame.available_metrics
    by_id = {r.gsis_id: r for r in loaded.rows}
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34, abs=1e-6)


def test_store_is_idempotent(db):
    _seed(db, COMPLETE_SF)
    frame = build_usage_weekly(db, 2025, 3)
    store_usage_weekly(db, frame)
    store_usage_weekly(db, frame)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.usage_weekly")
        assert cur.fetchone()[0] == 5


def test_load_on_an_unbuilt_week_raises(db):
    with pytest.raises(ValueError, match="no rows"):
        load_usage_weekly(db, 2025, 3)


def test_postseason_week_builds_without_snap_counts(db):
    # raw.nflverse_snap_counts is REG-only (max week 18); player_week runs to
    # week 22. Weeks 19+ must still build — snap_share is simply NULL, which
    # is a coverage boundary, not a partial publish.
    with db.cursor() as cur:
        for gsis_id, team, position, targets, carries in COMPLETE_SF:
            cur.execute(
                "INSERT INTO raw.nflverse_player_week "
                "(gsis_id, season, week, team, position, targets, carries) "
                "VALUES (%s, 2025, 20, %s, %s, %s, %s)",
                (gsis_id, team, position, targets, carries),
            )
    db.commit()
    frame = build_usage_weekly(db, 2025, 20)
    assert all(r.snap_share is None for r in frame.rows)
    assert all(r.games_complete == 1 for r in frame.rows)
    by_id = {r.gsis_id: r for r in frame.rows}
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34)


def test_frame_and_row_are_immutable(db):
    _seed(db, COMPLETE_SF)
    frame = build_usage_weekly(db, 2025, 3)
    assert isinstance(frame, UsageFrame) and isinstance(frame.rows[0], UsageRow)
    with pytest.raises(Exception):
        frame.rows[0].target_share = 0.5
