"""Drop -> clear state machine over the weekly calendar (ADR R4, R29).

A dropped player enters waivers for `waiver_period_days` and clears at the next
waiver-processing day on or after that. The clear TIME is derivable from safe
league-clock fields; the clear AWARD (FCFS vs rolling priority) is a P3 observed
mechanism that is still UNSET, so `next_clear` fails closed — it refuses to
name an award rather than guessing one.

Tz-aware and DST-safe: all arithmetic is done on calendar dates in the league
timezone, then re-attached to that timezone, so a drop on the Nov 1 DST
boundary still resolves to the correct local wall-clock day.
"""
from __future__ import annotations

import datetime

from ffi.league_state.clock import LeagueClock, WEEKDAYS
from ffi.waiver import ClearEvent

VALID_AWARDS = ("rolling_priority", "fcfs")


def _next_day_on_or_after(day: datetime.date, weekday_name: str) -> datetime.date:
    target = WEEKDAYS[weekday_name]
    offset = (target - day.weekday()) % 7
    return day + datetime.timedelta(days=offset)


def next_clear(
    drop_ts: datetime.datetime,
    clock: LeagueClock,
    award_mechanism: str | None = None,
) -> ClearEvent:
    """The clear event for a player dropped at `drop_ts`.

    `award_mechanism` is the (P3-observed) post-clear award. When it is None or
    not a known mechanism, the event refuses to name an award — but the clear
    TIME is still returned, because it is derivable from safe fields.
    """
    if drop_ts.tzinfo is None:
        raise ValueError("drop_ts must be tz-aware (ADR §7 timezones)")
    eligible = drop_ts.date() + datetime.timedelta(days=clock.waiver_period_days)
    clear_day = _next_day_on_or_after(eligible, clock.waivers_process_day)
    clears_at = datetime.datetime(
        clear_day.year, clear_day.month, clear_day.day, tzinfo=clock.timezone
    )
    if award_mechanism not in VALID_AWARDS:
        return ClearEvent(
            clears_at=clears_at,
            award="refused",
            reason=(
                "clear award mechanism is unset (P3) — refusing to name an award; "
                f"clear time is {clears_at:%Y-%m-%d} (derived from verified fields)"
            ),
        )
    return ClearEvent(clears_at=clears_at, award=award_mechanism)
