"""League-state package: clock, adapter, manual, reconcile."""
import datetime
import pathlib

import pytest
from zoneinfo import ZoneInfo

from ffi.league_state import RosterRow, Transaction
from ffi.league_state import adapter
from ffi.league_state import clock as clock_mod
from ffi.league_state import manual
from ffi.league_state import reconcile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")


def _clock() -> clock_mod.LeagueClock:
    return clock_mod.load(REPO_ROOT / "config" / "league_clock.yaml")


# --- clock -----------------------------------------------------------------
def test_clock_loads_verified_fields_only():
    c = _clock()
    assert c.league_id == 326814
    assert c.teams == 12
    assert c.waivers_process_day == "tuesday"
    assert c.waiver_period_days == 1
    assert c.season_first_game_date == datetime.date(2026, 9, 10)


def test_week_start_is_thursday_of_that_week():
    c = _clock()
    assert c.week_start(1).date() == datetime.date(2026, 9, 10)
    assert c.week_start(2).date() == datetime.date(2026, 9, 17)
    assert c.week_start(1).weekday() == 3  # Thursday


def test_waiver_deadline_is_the_tuesday_after_the_week():
    c = _clock()
    d = clock_mod.deadline("waivers", 1, c)
    assert d.date() == datetime.date(2026, 9, 15)  # Tuesday after week 1
    assert d.weekday() == 1
    assert d.tzinfo is not None


def test_season_start_and_week_start_events():
    c = _clock()
    assert clock_mod.deadline("season_start", 1, c).date() == datetime.date(2026, 9, 10)
    assert clock_mod.deadline("week_start", 1, c).date() == datetime.date(2026, 9, 10)


def test_window_week_is_a_full_seven_days():
    c = _clock()
    start, end = clock_mod.window("week", 1, c)
    assert (end - start) == datetime.timedelta(days=7)


def test_window_waivers_uses_period_days():
    c = _clock()
    start, end = clock_mod.window("waivers", 1, c)
    assert (end - start) == datetime.timedelta(days=1)


def test_fallback_fire_times_are_derived_from_the_boundary():
    c = _clock()
    claims = clock_mod.fallback_fire_time("claims", 1, c)
    trends = clock_mod.fallback_fire_time("trends", 1, c)
    assert claims == datetime.datetime(2026, 9, 14, 18, 0, tzinfo=LEAGUE_TZ)  # Monday
    assert trends == datetime.datetime(2026, 9, 15, 12, 0, tzinfo=LEAGUE_TZ)  # Tuesday


def test_clock_rejects_unknown_event_and_job():
    with pytest.raises(ValueError, match="unknown event"):
        clock_mod.deadline("nonsense", 1, _clock())
    with pytest.raises(ValueError, match="unknown job"):
        clock_mod.fallback_fire_time("nonsense", 1, _clock())


# --- manual ----------------------------------------------------------------
def test_manual_parses_exact_and_date_precision():
    text = (
        "league_id: 326814\n"
        "season: 2026\n"
        "week: 1\n"
        "---\n"
        "add|2026-09-09T08:15:00-07:00|3|40039\n"
        "drop|2026-09-09|7|40040\n"
    )
    rows = manual.parse_text(text)
    assert len(rows) == 2
    exact, date_only = rows
    assert exact.ts_precision == "exact"
    assert exact.ts.tzinfo is not None
    assert date_only.ts_precision == "date"
    assert date_only.ts.date() == datetime.date(2026, 9, 9)
    assert date_only.ts.tzinfo is not None


def test_manual_requires_league_and_season():
    with pytest.raises(manual.CaptureParseError, match="league_id"):
        manual.parse_text("add|2026-09-09T08:15:00-07:00|3|40039\n")


def test_manual_rejects_tz_naive_timestamp():
    text = "league_id: 326814\nseason: 2026\nadd|2026-09-09 08:15:00|3|40039\n"
    with pytest.raises(manual.CaptureParseError, match="tz-naive"):
        manual.parse_text(text)


def test_manual_rejects_unknown_kind():
    text = "league_id: 326814\nseason: 2026\nsteal|2026-09-09|3|40039\n"
    with pytest.raises(manual.CaptureParseError, match="steal"):
        manual.parse_text(text)


def test_manual_stable_source_transaction_id_is_idempotent():
    text = "league_id: 326814\nseason: 2026\nadd|2026-09-09T08:15:00-07:00|3|40039\n"
    a = manual.parse_text(text)[0]
    b = manual.parse_text(text)[0]
    assert a.source_transaction_id == b.source_transaction_id


# --- adapter (DB) ----------------------------------------------------------
def test_record_and_load_transactions(db):
    tx = Transaction(
        league_id=326814,
        season=2026,
        week=1,
        kind="add",
        source_transaction_id="manual-1",
        ts=datetime.datetime(2026, 9, 9, 8, 15, tzinfo=LEAGUE_TZ),
        team_id=3,
        payload={"player_id": "40039"},
    )
    adapter.record([tx], "manual", "exact", conn=db)
    rows = adapter.load_transactions(1, conn=db, league_id=326814)
    assert len(rows) == 1
    assert rows[0].source == "manual"
    assert rows[0].ts_precision == "exact"
    assert rows[0].kind == "add"


def test_record_transactions_is_idempotent(db):
    tx = Transaction(
        league_id=326814,
        season=2026,
        week=1,
        kind="add",
        source_transaction_id="manual-1",
        team_id=3,
    )
    adapter.record([tx], "manual", "date", conn=db)
    adapter.record([tx], "manual", "date", conn=db)
    assert len(adapter.load_transactions(1, conn=db)) == 1


def test_record_and_load_rosters(db):
    row = RosterRow(
        league_id=326814,
        season=2026,
        as_of=datetime.date(2026, 9, 9),
        team_id=1,
        player_id="40039",
        slot_type="starter",
        position="QB",
    )
    adapter.record([row], "manual", "date", conn=db)
    rows = adapter.load_rosters(datetime.date(2026, 9, 9), conn=db, league_id=326814)
    assert len(rows) == 1
    assert rows[0].slot_type == "starter"
    assert rows[0].source == "manual"


def test_record_rejects_bad_source(db):
    with pytest.raises(ValueError, match="source"):
        adapter.record([], "yolo", "exact", conn=db)


# --- reconcile -------------------------------------------------------------
def _tx(sid, kind="add", team=3, ts=None):
    return Transaction(
        league_id=326814,
        season=2026,
        week=1,
        kind=kind,
        source_transaction_id=sid,
        ts=ts,
        team_id=team,
        payload={"player_id": "40039"},
    )


def test_reconcile_detects_manual_only_and_api_only():
    t = datetime.datetime(2026, 9, 9, 8, 15, tzinfo=LEAGUE_TZ)
    manual_rows = [_tx("m1", ts=t)]
    api_rows = [_tx("a1", team=4, ts=t)]
    result = reconcile.reconcile(manual_rows, api_rows)
    outcomes = {d.outcome for d in result.diffs}
    assert outcomes == {"manual_only", "api_only"}
    assert result.matched == 0


def test_reconcile_matches_same_move_and_flags_ts_mismatch():
    t1 = datetime.datetime(2026, 9, 9, 8, 15, tzinfo=LEAGUE_TZ)
    t2 = datetime.datetime(2026, 9, 9, 9, 0, tzinfo=LEAGUE_TZ)
    result = reconcile.reconcile([_tx("m1", ts=t1)], [_tx("a1", ts=t2)])
    assert result.matched == 0
    assert [d.outcome for d in result.diffs] == ["mismatch"]


def test_reconcile_does_not_false_mismatch_on_date_precision():
    t1 = datetime.datetime(2026, 9, 9, 0, 0, tzinfo=LEAGUE_TZ)
    t2 = datetime.datetime(2026, 9, 9, 18, 0, tzinfo=LEAGUE_TZ)

    def _with_prec(t, prec):
        return Transaction(
            league_id=326814,
            season=2026,
            week=1,
            kind="add",
            source_transaction_id="x",
            ts=t,
            team_id=3,
            payload={"player_id": "40039"},
            ts_precision=prec,
        )

    result = reconcile.reconcile(
        [_with_prec(t1, "date")], [_with_prec(t2, "exact")]
    )
    assert result.matched == 1
    assert result.diffs == ()
