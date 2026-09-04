"""Single runtime reader of `config/league_clock.yaml` (ADR Domain 2).

Every scheduled job time is DERIVED from a deadline in this file rather than
hardcoded, so a cadence/deadline mismatch is a config error instead of a
structural defeat (R2). `scripts/validate_league_clock.py` enforces the other
half of the contract: no module may consume an UNSET or UNVERIFIED field. This
module therefore loads ONLY the fields that are structurally stable and
verified, and never names the P3 pending mechanics (processing hour, clear
award mechanism, reset boundary, or the unverified trade/playoff fields).

Fail-closed: a deadline that would need an UNSET field is simply not derivable
here — it is not exposed, so a consumer cannot silently compute on a guessed
number.
"""
from __future__ import annotations

import datetime
import pathlib
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import yaml

DEFAULT_PATH = pathlib.Path("config/league_clock.yaml")

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Hard-fallback fire times, relative to the (hour-UNSET) waiver boundary. These
# are conservative launchd fallbacks, not the primary trigger: the exact
# processing hour is a P3 observed value, so the fallback fires early enough
# (claims) / late enough (trends) to be safe on either side of it.
CLAIMS_LEAD_HOURS = 6   # Monday 18:00 local = Tuesday 00:00 boundary minus 6h
TRENDS_LAG_HOURS = 12   # Tuesday 12:00 local = Tuesday 00:00 boundary plus 12h


@dataclass(frozen=True)
class LeagueClock:
    as_of: datetime.date
    timezone: ZoneInfo
    league_id: int
    teams: int
    waiver_type: str
    waivers_process_day: str  # lowercase weekday name
    waiver_period_days: int
    weekly_acquisition_limit: int
    season_acquisition_limit: int
    ir_direct_add: bool
    season_first_game_date: datetime.date

    def _weekday_offset(self, target_day: str) -> int:
        """Days from the season's first game day (a Thursday) to `target_day`,
        wrapping forward — so 'tuesday' resolves to the Tuesday AFTER the week's
        games, not the one before."""
        anchor = self.season_first_game_date.weekday()
        return (WEEKDAYS[target_day] - anchor) % 7

    def week_start(self, week: int) -> datetime.datetime:
        """The game-week's opening day (Thursday of that week), 00:00 local."""
        if week < 1:
            raise ValueError(f"week must be >= 1, got {week}")
        day = self.season_first_game_date + datetime.timedelta(days=7 * (week - 1))
        return datetime.datetime(day.year, day.month, day.day, tzinfo=self.timezone)


def load(path: pathlib.Path | None = None) -> LeagueClock:
    p = path or DEFAULT_PATH
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict) or "as_of" not in raw:
        raise ValueError(f"{p}: expected a mapping with an 'as_of' key")

    def _need(key: str):
        if key not in raw or raw[key] is None:
            raise ValueError(f"{p}: missing required league-clock field {key!r}")
        return raw[key]

    tz = ZoneInfo(_need("timezone"))
    day = str(_need("waivers_process_day")).lower()
    if day not in WEEKDAYS:
        raise ValueError(
            f"{p}: waivers_process_day {day!r} not one of {sorted(WEEKDAYS)}"
        )
    return LeagueClock(
        as_of=datetime.date.fromisoformat(str(_need("as_of"))),
        timezone=tz,
        league_id=int(_need("league_id")),
        teams=int(_need("teams")),
        waiver_type=str(_need("waiver_type")),
        waivers_process_day=day,
        waiver_period_days=int(_need("waiver_period_days")),
        weekly_acquisition_limit=int(_need("weekly_acquisition_limit")),
        season_acquisition_limit=int(_need("season_acquisition_limit")),
        ir_direct_add=bool(_need("ir_direct_add")),
        season_first_game_date=datetime.date.fromisoformat(
            str(_need("season_first_game_date"))
        ),
    )


def deadline(
    event: str, week: int, clock: LeagueClock | None = None
) -> datetime.datetime:
    """Tz-aware datetime for a named calendar event.

    Events:
      'season_start' — the league's first game day, 00:00 local.
      'week_start'   — the opening day of game week `week`, 00:00 local.
      'waivers'      — the waiver-processing boundary following week `week`
                       (the processing DAY at 00:00 local; the exact hour is a
                       P3 observed value this module refuses to guess).
    """
    c = clock or load()
    if event == "season_start":
        d = c.season_first_game_date
        return datetime.datetime(d.year, d.month, d.day, tzinfo=c.timezone)
    if event == "week_start":
        return c.week_start(week)
    if event == "waivers":
        if week < 1:
            raise ValueError(f"week must be >= 1, got {week}")
        start = c.week_start(week)
        day = start + datetime.timedelta(days=c._weekday_offset(c.waivers_process_day))
        return datetime.datetime(day.year, day.month, day.day, tzinfo=c.timezone)
    raise ValueError(
        f"unknown event {event!r} (known: season_start, week_start, waivers)"
    )


def window(
    event: str, week: int, clock: LeagueClock | None = None
) -> tuple[datetime.datetime, datetime.datetime]:
    """(start, end) for a named event window.

    'week'    -> the full game week [week_start(week), week_start(week+1)).
    'waivers' -> the waiver period [waiver boundary, boundary + period_days).
    """
    c = clock or load()
    if event == "week":
        return c.week_start(week), c.week_start(week + 1)
    if event == "waivers":
        start = deadline("waivers", week, c)
        end = start + datetime.timedelta(days=c.waiver_period_days)
        return start, end
    raise ValueError(f"unknown event {event!r} (known: week, waivers)")


def fallback_fire_time(
    job: str, week: int, clock: LeagueClock | None = None
) -> datetime.datetime:
    """Hard-fallback launchd fire time for a named job.

    'claims' -> Monday 18:00 local (before the Tuesday waiver boundary).
    'trends' -> Tuesday 12:00 local (after the Tuesday waiver boundary).
    """
    c = clock or load()
    boundary = deadline("waivers", week, c)
    if job == "claims":
        return boundary - datetime.timedelta(hours=CLAIMS_LEAD_HOURS)
    if job == "trends":
        return boundary + datetime.timedelta(hours=TRENDS_LAG_HOURS)
    raise ValueError(f"unknown job {job!r} (known: claims, trends)")
