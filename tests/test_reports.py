"""Reports: claims + trends render through render_or_refuse."""
import json

from ffi.health import SourceState
from ffi.reports.claims import _latest_adds, _roster_sleeper_ids, render_claims
from ffi.reports.trends import render_trends


def test_claims_refuses_on_broken_input():
    out = render_claims(1, inputs={"sleeper_trending": SourceState.BROKEN})
    assert out.startswith("NO SIGNAL —")
    assert "sleeper_trending BROKEN" in out


def test_claims_renders_header_when_all_ok():
    out = render_claims(1, inputs={"sleeper_trending": SourceState.OK}, conn=None)
    assert "# Claims Brief — Week 1" in out
    assert "Waiver deadline:" in out


def test_claims_requires_conn_or_inputs():
    import pytest

    with pytest.raises(ValueError, match="conn or an explicit inputs"):
        render_claims(1)


def test_trends_refuses_on_broken_usage():
    out = render_trends(1, inputs={"nflverse_player_week": SourceState.BROKEN})
    assert out.startswith("NO SIGNAL —")
    assert "nflverse_player_week BROKEN" in out


def test_trends_renders_signals_and_angles():
    from ffi.trade_angles import Angle
    from ffi.usage import ASCENDING, TrendSignal

    sig = TrendSignal("G1", ASCENDING, "snap_rise_2wk", "snap 60% -> 70%", False)
    angle = Angle("opponent_need", "RB", None, 3, "team 3 thin at RB")
    out = render_trends(
        2, inputs={"nflverse_player_week": SourceState.OK}, signals=[sig], angles=[angle]
    )
    assert "# Trends & Targets — Week 2" in out
    assert "[ASCENDING] G1" in out
    assert "[opponent_need]" in out


# --- db-backed claims brief -------------------------------------------------
def _seed_add_snapshot(db, rows, archive_date="2026-09-14"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.sleeper_trending "
            "(run_id, archive_date, trend_type, lookback_hours, payload) "
            "VALUES (NULL, %s, 'add', 24, %s)",
            (archive_date, json.dumps(rows)),
        )
    db.commit()


def test_latest_adds_reads_the_freshest_snapshot(db):
    _seed_add_snapshot(db, [{"player_id": "111", "count": 10}], "2026-09-13")
    _seed_add_snapshot(db, [{"player_id": "222", "count": 20}], "2026-09-14")
    adds, archive_date = _latest_adds(db)
    assert adds == [("222", 20)]
    assert archive_date == "2026-09-14"


def test_latest_adds_with_no_archive_is_empty(db):
    assert _latest_adds(db) == ([], "")


def test_roster_sleeper_ids_maps_yahoo_and_direct_sleeper_ids(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk "
            "(name, position, team, yahoo_id, sleeper_id) "
            "VALUES ('Taken Player', 'RB', 'KC', '9002', '222')"
        )
        cur.execute(
            "INSERT INTO public.league_rosters "
            "(league_id, season, as_of, team_id, player_id, position, slot_type, "
            "source, ts_precision) VALUES "
            "(326814, 2026, '2026-09-14', 5, '9002', 'RB', 'starter', 'manual', 'date'), "
            "(326814, 2026, '2026-09-14', 6, '777', 'WR', 'bench', 'manual', 'date')"
        )
    db.commit()
    taken = _roster_sleeper_ids(db, 326814)
    assert "222" in taken  # via the yahoo->sleeper crosswalk mapping
    assert "777" in taken  # rookie rows carry sleeper ids directly
    assert "9002" in taken  # the raw yahoo id is in the set too (harmless)


def test_claims_renders_targets_and_cutline(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk "
            "(name, position, team, yahoo_id, sleeper_id) VALUES "
            "('Hot Free Agent', 'WR', 'LV', '9001', '111'), "
            "('Taken Player', 'RB', 'KC', '9002', '222'), "
            "('My Bench Guy', 'WR', 'PIT', '9003', '333')"
        )
        cur.execute(
            "SELECT xwalk_id FROM public.player_id_xwalk WHERE yahoo_id='9003'"
        )
        bench_xwalk = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO scoring.config (version, description, rules) "
            "VALUES (99, 'test', '{}')"
        )
        cur.execute(
            "INSERT INTO valuation.player_value "
            "(config_version, scenario, xwalk_id, position, proj_points, vorp, params) "
            "VALUES (99, 'qb_hoard_12', %s, 'WR', 100, 5.5, '{}')",
            (bench_xwalk,),
        )
        cur.execute(
            "INSERT INTO public.league_rosters "
            "(league_id, season, as_of, team_id, player_id, position, slot_type, "
            "source, ts_precision) VALUES "
            "(326814, 2026, '2026-09-14', 5, '9002', 'RB', 'starter', 'manual', 'date'), "
            "(326814, 2026, '2026-09-14', 12, '9003', 'WR', 'bench', 'manual', 'date')"
        )
    _seed_add_snapshot(
        db,
        [
            {"player_id": "111", "count": 500},
            {"player_id": "222", "count": 400},
            {"player_id": "999", "count": 300},
        ],
    )
    out = render_claims(
        1,
        conn=db,
        inputs={
            "nflverse_player_week": SourceState.OK,
            "sleeper_trending": SourceState.OK,
        },
    )
    assert "Hot Free Agent (WR)" in out
    assert "500 adds" in out
    assert "Taken Player" not in out  # on team 5's roster -> filtered out
    assert "My Bench Guy (WR) — vorp 5.5" in out
    assert "Adding a WR displaces: My Bench Guy (vorp 5.5)" in out
    assert "not in the crosswalk" in out  # sleeper 999 has no xwalk row


def test_trends_renders_market_and_angles_from_db(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk "
            "(name, position, team, yahoo_id, sleeper_id) VALUES "
            "('Hot Free Agent', 'WR', 'LV', '9001', '111')"
        )
        cur.execute(
            "INSERT INTO public.league_rosters "
            "(league_id, season, as_of, team_id, player_id, position, slot_type, "
            "source, ts_precision) VALUES "
            "(326814, 2026, '2026-09-14', 1, '9001', 'WR', 'starter', 'manual', 'date'), "
            "(326814, 2026, '2026-09-14', 1, 'Q1', 'QB', 'starter', 'manual', 'date')"
        )
    db.commit()
    _seed_add_snapshot(db, [{"player_id": "111", "count": 500}])
    out = render_trends(1, conn=db, inputs={"nflverse_player_week": SourceState.OK})
    assert "Hot Free Agent (WR)" in out
    assert "500 adds" in out
    assert "ON A NAJEE ROSTER" in out  # player 111 is on team 1
    assert "[qb_repair]" in out  # team 1 has one QB in a 2-QB league
    assert "2026 usage is not ingested" in out  # no usage_weekly 2026 rows
