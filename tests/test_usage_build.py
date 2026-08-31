import dataclasses

import pytest
import structlog

from ffi.usage import METRICS
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


def test_low_play_but_fully_published_team_week_still_computes_shares(db):
    # The KC-week-18 profile: 30 player rows, 22 targets + 15 carries = 37
    # plays. Under the floor on plays, far over it on players — a real rest
    # week, fully published. The conjunction must let it through, or ~33
    # players per incident lose their shares for no reason.
    rows = [(f"00-000{i:04d}", "KC", "WR", 1, 0) for i in range(22)]
    rows += [(f"00-000{i:04d}", "KC", "RB", 0, 2) for i in range(22, 29)]
    rows += [("00-0000029", "KC", "QB", 0, 1)]
    _seed(db, rows)
    frame = build_usage_weekly(db, 2025, 3)
    assert len(frame.rows) == 30
    by_id = {r.gsis_id: r for r in frame.rows}
    assert (by_id["00-0000000"].team_targets, by_id["00-0000000"].team_carries) == (
        22,
        15,
    )
    assert all(r.games_complete == 1 for r in frame.rows)
    assert by_id["00-0000000"].target_share == pytest.approx(1 / 22)
    assert by_id["00-0000022"].carry_share == pytest.approx(2 / 15)


def test_partial_team_week_refuses_to_compute_shares(db):
    # 6 targets + 3 carries = 9 plays across ONE player row: nflverse is
    # mid-publish, not the 49ers having run nine plays. A short denominator
    # here would put that WR at 100% target share and fire a false ASCENDING
    # for the whole team. Both halves of the conjunction trip.
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
    # Seed a snap row: with an empty snap feed snap_share would also be
    # disabled (see test_postseason_...), and this test is about the two
    # metrics that are STRUCTURALLY unavailable in Plan 1.
    _seed(db, COMPLETE_SF, snaps=[("00-0000001", "SF", "WR", 0.82)])
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
    # An all-NULL column must be announced in the frame, not only in the log:
    # Task 9 disables a rule by `requires` against available_metrics, so a
    # snap_share left in there would let a snap rule run over 397 NULLs.
    assert "snap_share" in frame.disabled_metrics
    assert "snap_share" not in frame.available_metrics
    assert frame.available_metrics == frozenset({"target_share", "carry_share"})
    assert set(METRICS) == frame.available_metrics | set(frame.disabled_metrics)


def test_null_offense_pct_yields_null_snap_share(db):
    # The nullable-column deviation: build reads `snaps.get(id) is None`, not
    # `id not in snaps`. A snap row that EXISTS with a NULL offense_pct is
    # reachable in the live feed; under `not in` it reaches float(None) and
    # takes down the whole week's build. _seed can't express this (it
    # multiplies pct), so insert the NULL directly. Reverting the guard to
    # `not in` makes this test raise TypeError.
    _seed(db, COMPLETE_SF, snaps=[("00-0000002", "SF", "WR", 0.55)])
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.nflverse_snap_counts "
            "(gsis_id, season, week, team, position, offense_snaps, offense_pct) "
            "VALUES ('00-0000001', 2025, 3, 'SF', 'WR', NULL, NULL)"
        )
    db.commit()
    frame = build_usage_weekly(db, 2025, 3)
    by_id = {r.gsis_id: r for r in frame.rows}
    assert by_id["00-0000001"].snap_share is None
    # The rest of the week is unaffected — one NULL is not a failed build.
    assert by_id["00-0000002"].snap_share == pytest.approx(0.55)
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34)


def test_all_null_snap_feed_disables_snap_share_and_survives_a_round_trip(db):
    # The shape the `if not snaps` predicate missed: the snap feed HAS published
    # rows for this week, but every offense_pct in them is NULL. build saw a
    # non-empty map and left snap_share advertised; load, which looks at the
    # stored rows, disabled it. Same week, same data, two different frames —
    # a rule would run on Tuesday and be disabled on Wednesday. Both paths now
    # decide off the assembled rows, so the round trip is an identity.
    _seed(db, COMPLETE_SF)
    with db.cursor() as cur:
        for gsis_id, _, _, _, _ in COMPLETE_SF:
            cur.execute(
                "INSERT INTO raw.nflverse_snap_counts "
                "(gsis_id, season, week, team, position, offense_snaps, offense_pct) "
                "VALUES (%s, 2025, 3, 'SF', 'WR', NULL, NULL)",
                (gsis_id,),
            )
    db.commit()
    frame = build_usage_weekly(db, 2025, 3)
    assert all(r.snap_share is None for r in frame.rows)
    assert "snap_share" in frame.disabled_metrics
    assert frame.available_metrics == frozenset({"target_share", "carry_share"})
    # Shares that do NOT depend on the snap feed are untouched.
    by_id = {r.gsis_id: r for r in frame.rows}
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34)

    store_usage_weekly(db, frame)
    loaded = load_usage_weekly(db, 2025, 3)
    # The identity property itself, asserted directly.
    assert loaded.available_metrics == frame.available_metrics
    assert loaded.disabled_metrics == frame.disabled_metrics


def test_load_disables_snap_share_on_a_stored_all_null_regular_season_week(db):
    # Load's own decision, isolated from build: rows land in usage_weekly with
    # snap_share NULL and week 3 (regular season, so this is a feed outage, not
    # the postseason coverage boundary). Load must disable the metric AND say
    # which of the two it is — a postseason-worded line on a week-3 outage
    # would read as expected-and-fine and nobody would chase the feed.
    with db.cursor() as cur:
        for i, (gsis_id, team, position, *_) in enumerate(COMPLETE_SF):
            cur.execute(
                "INSERT INTO public.usage_weekly (gsis_id, season, week, team, "
                "position, snap_share, target_share, carry_share, route_share, "
                "rz_touches, team_targets, team_carries, games_complete, "
                "teams_observed, computed_at) VALUES (%s, 2025, 3, %s, %s, NULL, "
                "%s, NULL, NULL, NULL, 34, 26, 1, 1, now())",
                (gsis_id, team, position, (i + 1) / 34),
            )
    db.commit()
    with structlog.testing.capture_logs() as logs:
        frame = load_usage_weekly(db, 2025, 3)
    assert "snap_share" in frame.disabled_metrics
    assert "snap_share" not in frame.available_metrics
    assert set(METRICS) == frame.available_metrics | set(frame.disabled_metrics)
    assert frame.available_metrics == frozenset({"target_share", "carry_share"})
    entry = next(e for e in logs if e["event"] == "usage.no_snap_counts")
    assert entry["log_level"] == "warning"
    assert entry["path"] == "load"
    assert "postseason" not in entry["note"]


def test_frame_and_row_are_frozen(db):
    _seed(db, COMPLETE_SF)
    frame = build_usage_weekly(db, 2025, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.rows[0].target_share = 0.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.teams_observed = 99
