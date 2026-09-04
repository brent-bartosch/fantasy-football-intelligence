#!/usr/bin/env python3
"""launchd entry point for com.ffi.claims (Monday evening).

Renders the claims brief and writes `reports/claims-YYYY-WW.md`. Exits nonzero
on any failure so launchd surfaces a missed brief — a missed claim deadline is
not recoverable.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import sys
from zoneinfo import ZoneInfo

from ffi import db
from ffi.league_state.clock import load as load_clock
from ffi.reports.claims import render_claims

LEAGUE_TZ = ZoneInfo("America/Los_Angeles")
REPORTS_DIR = pathlib.Path("reports")


def _current_week(clock, now) -> int:
    d = now.astimezone(LEAGUE_TZ).date()
    for week in range(1, 20):
        if clock.week_start(week).date() <= d < clock.week_start(week + 1).date():
            return week
    raise SystemExit("today is outside the regular-season window (weeks 1-19)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", type=int, help="override the derived game week")
    args = parser.parse_args()

    clock = load_clock()
    week = args.week or _current_week(
        clock, datetime.datetime.now(datetime.timezone.utc)
    )
    conn = db.connect()
    try:
        text = render_claims(week, conn=conn)
    finally:
        conn.close()

    season = clock.season_first_game_date.year
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"claims-{season}-{week:02d}.md"
    out.write_text(text)
    print(f"OK {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
