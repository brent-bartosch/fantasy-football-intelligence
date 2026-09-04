"""trade_angles.py: opponent needs, buy-low/sell-high, QB repair."""
import datetime

from ffi.league_profile import NAJEE
from ffi.league_state import RosterRow
from ffi.trade_angles import buy_low_sell_high, opponent_needs, qb_repair
from ffi.usage import ASCENDING, FALLING, TrendSignal
from ffi.usage.market import MarketState


def _roster(*rows):
    return [
        RosterRow(
            league_id=326814,
            season=2026,
            as_of=datetime.date(2026, 9, 9),
            team_id=t,
            player_id=f"{pos}{i}",
            slot_type="bench",
            position=pos,
        )
        for t, pos, i in rows
    ]


def test_opponent_needs_flags_thin_positions():
    rosters = _roster(
        (1, "QB", 1),
        (1, "QB", 2),
        (1, "RB", 1),
        (2, "QB", 1),
    )
    out = opponent_needs(rosters, NAJEE)
    # team 1 is thin at RB (need 3, has 1) and every other non-QB position.
    rb = [a for a in out if a.target_team_id == 1 and a.position == "RB"]
    assert rb and rb[0].kind == "opponent_need"


def test_qb_repair_flags_team_below_qb_starters():
    rosters = _roster((1, "QB", 1), (1, "RB", 1), (2, "QB", 1), (2, "QB", 2))
    out = qb_repair(rosters, NAJEE)  # NAJEE starts 2 QB
    teams = {a.target_team_id for a in out}
    assert teams == {1}  # team 1 has one QB; team 2 has two


def test_buy_low_flags_ascending_cold_market():
    sig = TrendSignal("G1", ASCENDING, "snap_rise_2wk", "snap 60% -> 70%", False)
    market = {"G1": MarketState(add_count=2, drop_count=3)}  # net -1
    out = buy_low_sell_high([sig], market)
    assert [a.kind for a in out] == ["buy_low"]


def test_sell_high_flags_falling_hot_market():
    sig = TrendSignal("G1", FALLING, "route_collapse", "route 50% -> 20%", False)
    market = {"G1": MarketState(add_count=7, drop_count=0)}  # net 7 >= NET_HOT
    out = buy_low_sell_high([sig], market)
    assert [a.kind for a in out] == ["sell_high"]


def test_buy_low_sell_high_skips_failed_market_gate():
    sig = TrendSignal("G1", ASCENDING, "snap_rise_2wk", "evidence", False)
    market = {"G1": MarketState(add_count=0, drop_count=5, gate_failed=True)}
    assert buy_low_sell_high([sig], market) == []
