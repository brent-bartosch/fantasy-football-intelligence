"""Drop -> clear state machine over the OBSERVED weekly mechanics (R4, R29).

FITTED MODEL (2026-09-14, from the 2025 transaction log — see
config/league_clock.yaml): a dropped player sits on waivers for
`waiver_period_days`; if unclaimed, they clear to FREE AGENCY at the same
local clock time `waiver_period_days` later — NOT batch-gated, FCFS. A
claim filed during the window resolves at the weekly batch instead (that is
window 1, not this module). Nobody in the league currently snipes the clear
moment (fastest observed re-add: 39.3h after the drop), so the watcher
built on this model races an unguarded window.

DST note (R29): the clear is computed on the calendar — same wall-clock
time next day. On transition weekends the absolute-24h reading differs by
one hour; a watcher should poll from an hour before the computed moment.
"""
from __future__ import annotations

import datetime

from ffi.league_state.clock import LeagueClock
from ffi.waiver import ClearEvent

_VALID_AWARDS = ("fcfs", "rolling_priority")
_IMPLEMENTED_BEHAVIOR = "clear_at_24h"


def next_clear(drop_ts: datetime.datetime, clock: LeagueClock) -> ClearEvent:
    """The moment a dropped player becomes a grabbable free agent."""
    if drop_ts.tzinfo is None:
        raise ValueError("drop_ts must be tz-aware (ADR §7 timezones)")
    if clock.weekend_drop_clear_behavior != _IMPLEMENTED_BEHAVIOR:
        raise ValueError(
            f"clear_time implements only {_IMPLEMENTED_BEHAVIOR!r}; "
            f"league_clock.yaml declares {clock.weekend_drop_clear_behavior!r} "
            "— update the config or the model, never guess (fail-loud)"
        )
    if clock.clear_award_mechanism not in _VALID_AWARDS:
        raise ValueError(
            f"unknown clear award mechanism {clock.clear_award_mechanism!r} "
            f"(expected one of {_VALID_AWARDS})"
        )
    local = drop_ts.astimezone(clock.timezone)
    clear_day = local.date() + datetime.timedelta(days=clock.waiver_period_days)
    clears_at = datetime.datetime.combine(
        clear_day, local.time(), tzinfo=clock.timezone
    )
    return ClearEvent(clears_at=clears_at, award=clock.clear_award_mechanism)