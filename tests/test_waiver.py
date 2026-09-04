"""Waiver package: cutline, priority, clear_time, ledger, cards."""
import datetime
import pathlib

import pytest
from zoneinfo import ZoneInfo

from ffi.league_state import clock as clock_mod
from ffi.waiver import (
    Claim,
    ClearEvent,
    ContingencyCard,
    CutlineRow,
    Decision,
    RosterSlot,
)
from ffi.waiver import cards, clear_time, cutline, ledger, priority

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")


def _clock():
    return clock_mod.load(REPO_ROOT / "config" / "league_clock.yaml")


def _slot(pid, pos, slot, value):
    return RosterSlot(player_id=pid, position=pos, slot_type=slot, value=value)


# --- cutline ---------------------------------------------------------------
def test_cutline_prefers_same_position_drop():
    roster = [
        _slot("QB1", "QB", "starter", 30),
        _slot("QB2", "QB", "bench", 10),
        _slot("RB1", "RB", "bench", 25),
    ]
    row = cutline.cutline(roster, "QB")
    assert row.worst_droppable == "QB2"
    assert row.worst_value == 10


def test_cutline_falls_back_to_worst_bench_when_no_same_position():
    roster = [
        _slot("QB1", "QB", "starter", 30),
        _slot("RB1", "RB", "bench", 25),
        _slot("WR1", "WR", "bench", 5),
    ]
    row = cutline.cutline(roster, "QB")
    assert row.worst_droppable == "WR1"


def test_cutline_empty_droppable_is_none():
    roster = [_slot("QB1", "QB", "starter", 30)]
    row = cutline.cutline(roster, "QB")
    assert not row.has_droppable
    assert not cutline.worth_adding(99, row)


def test_cutline_rejects_unknown_position():
    with pytest.raises(ValueError, match="position"):
        cutline.cutline([], "X")


def test_worth_adding_is_strictly_greater():
    row = CutlineRow("QB", "QB2", 10.0, 1)
    assert cutline.worth_adding(10.1, row)
    assert not cutline.worth_adding(10.0, row)


# --- priority --------------------------------------------------------------
def test_priority_skips_negative_net_value():
    c = Claim("P", "RB", value=0.4, drop_cost=0.5)
    d = priority.price(c, priority_pos=5, weeks_remaining=10)
    assert d.action == "skip"


def test_priority_claims_when_no_weeks_left():
    c = Claim("P", "RB", value=0.9, drop_cost=0.0)
    assert priority.price(c, priority_pos=1, weeks_remaining=0).action == "claim"


def test_priority_is_selective_with_good_position():
    # net 0.5: first in line (selective) waits, even though it's positive.
    marginal = Claim("P", "RB", value=0.5, drop_cost=0.0)
    assert priority.price(marginal, priority_pos=1, weeks_remaining=10).action == "wait"
    # net 0.7: worth spending even a bad (last-ish) position on.
    strong = Claim("P", "RB", value=0.7, drop_cost=0.0)
    assert priority.price(strong, priority_pos=14, weeks_remaining=10).action == "claim"


def test_priority_rejects_zero_priority():
    with pytest.raises(ValueError, match="priority_pos"):
        priority.price(Claim("P", "RB", 0.5, 0.0), 0, 10)


# --- clear_time ------------------------------------------------------------
def test_clear_time_refuses_unset_award():
    drop = datetime.datetime(2026, 9, 9, 12, 0, tzinfo=LEAGUE_TZ)
    ev = clear_time.next_clear(drop, _clock())
    assert ev.award == "refused"
    assert ev.reason is not None


def test_clear_time_derives_clear_day_from_safe_fields():
    # Dropped Monday of week 1 (Sep 14): 1-day waiver -> eligible Sep 15,
    # which IS a Tuesday -> clears Sep 15.
    drop = datetime.datetime(2026, 9, 14, 12, 0, tzinfo=LEAGUE_TZ)
    ev = clear_time.next_clear(drop, _clock(), award_mechanism="rolling_priority")
    assert ev.clears_at.date() == datetime.date(2026, 9, 15)
    assert ev.award == "rolling_priority"


def test_clear_time_is_dst_safe():
    # Drop on the Nov 1 DST boundary (Sunday). The 1-day waiver -> eligible
    # Monday Nov 2; next Tuesday -> Nov 3.
    drop = datetime.datetime(2026, 11, 1, 20, 0, tzinfo=LEAGUE_TZ)
    ev = clear_time.next_clear(drop, _clock(), award_mechanism="fcfs")
    assert ev.clears_at.date() == datetime.date(2026, 11, 3)
    assert ev.clears_at.tzinfo is not None


def test_clear_time_rejects_naive_drop():
    with pytest.raises(ValueError, match="tz-aware"):
        clear_time.next_clear(datetime.datetime(2026, 9, 14, 12, 0), _clock())


# --- ledger ----------------------------------------------------------------
def test_remaining_applies_safety_margin_and_floors():
    assert ledger.remaining(5, 0) == 4  # 5 - 0 - 1 margin
    assert ledger.remaining(5, 4) == 0  # floor, not negative
    assert ledger.remaining(6, 2, safety_margin=0) == 4


def test_remaining_rejects_negative_inputs():
    with pytest.raises(ValueError, match=">= 0"):
        ledger.remaining(-1, 0)


def test_diff_is_balanced_when_observed_matches_ledger():
    d = ledger.diff({1: 3, 2: 1}, {1: 3, 2: 1})
    assert d.balanced
    assert d.diffs == ()


def test_diff_reports_team_differences():
    d = ledger.diff({1: 3, 2: 1}, {1: 2, 2: 1, 3: 5})
    assert not d.balanced
    assert set(d.diffs) == {(1, 2, 3), (3, 5, 0)}


def test_week_for_maps_date_to_game_week():
    c = _clock()
    assert ledger._week_for(c, datetime.date(2026, 9, 10)) == 1
    assert ledger._week_for(c, datetime.date(2026, 9, 17)) == 2


def test_reconcile_requires_week():
    with pytest.raises(ValueError, match="week"):
        ledger.reconcile({1: 0})


# --- cards -----------------------------------------------------------------
def test_cards_cover_starter_without_backup():
    roster = [
        _slot("QB1", "QB", "starter", 30),
        _slot("RB1", "RB", "starter", 28),
        _slot("RB2", "RB", "bench", 12),
    ]
    out = cards.build_cards(7, roster, {"QB": ["QBF1", "QBF2"], "RB": ["RBF1"]})
    assert len(out) == 1
    assert out[0].starter_player_id == "QB1"
    assert out[0].contingency_player_id == "QBF1"
    assert out[0].position == "QB"


def test_cards_skip_covered_positions_and_missing_fa():
    roster = [
        _slot("QB1", "QB", "starter", 30),
        _slot("QB2", "QB", "bench", 10),
    ]
    assert cards.build_cards(7, roster, {"QB": ["QBF1"]}) == []
    assert cards.build_cards(7, roster, {}) == []
