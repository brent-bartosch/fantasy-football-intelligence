"""Canonical stat line: the single vocabulary every source adapter maps into.
None = the source does not carry that stat (distinct from 0 = observed zero)."""
from pydantic import BaseModel, ConfigDict


class StatLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Offense
    pass_completions: float | None = None
    pass_incompletions: float | None = None
    pass_yards: float | None = None
    pass_tds: float | None = None
    interceptions: float | None = None
    pick_sixes: float | None = None
    rush_attempts: float | None = None
    rush_yards: float | None = None
    rush_tds: float | None = None
    rush_first_downs: float | None = None
    receptions: float | None = None
    rec_yards: float | None = None
    rec_tds: float | None = None
    rec_first_downs: float | None = None
    return_yards: float | None = None
    return_tds: float | None = None
    two_point_conversions: float | None = None
    fumbles: float | None = None
    fumbles_lost: float | None = None
    offensive_fumble_return_tds: float | None = None
    # Kicking
    fg_0_19: float | None = None
    fg_20_29: float | None = None
    fg_30_39: float | None = None
    fg_40_49: float | None = None
    fg_50_plus: float | None = None
    fg_miss_0_19: float | None = None
    fg_miss_20_29: float | None = None
    fg_miss_30_39: float | None = None
    pat_made: float | None = None
    pat_missed: float | None = None
    # Defense/ST
    sacks: float | None = None
    def_interceptions: float | None = None
    fumble_recoveries: float | None = None
    defensive_tds: float | None = None
    safeties: float | None = None
    blocked_kicks: float | None = None
    fourth_down_stops: float | None = None
    tackles_for_loss: float | None = None
    three_and_outs: float | None = None
    extra_point_returns: float | None = None
    points_allowed: float | None = None
    yards_allowed: float | None = None
