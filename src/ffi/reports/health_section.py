"""The briefing's health-section renderers: artifact freshness, archive
continuity, expected-season assertions (ADR Domain 5).

Extracted from `scripts/morning_briefing.py` when that file hit its 400-line
budget (ARCHITECTURE §1b). These are the render-loop functions R1 lived in, so
they stay together and stay directly testable: a green `ffi.health` unit suite
proves nothing about the text an operator reads at 07:00.

Layer 4 leaf-ish: imports `ffi.health` only. No DB connection is opened here —
callers pass a live connection in (ARCHITECTURE §3a: only `ffi.db` connects).
"""

from __future__ import annotations

import datetime
import fnmatch
import pathlib
from zoneinfo import ZoneInfo

from ffi import health

LEAGUE_TZ = ZoneInfo("America/Los_Angeles")
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def mark(state: health.SourceState) -> str:
    return {
        health.SourceState.OK: "OK",
        health.SourceState.KNOWN_LAGGING: "LAG",
        health.SourceState.BROKEN: "RED",
    }[state]


def artifact_freshness_lines(clock, now_local, reports_dir):
    """(render_lines, red_flags) for every artifact contract due today.

    Before an artifact's `active_from` the line renders PENDING and never
    reds: asserting on a report whose renderer has not shipped is the banner
    fatigue R12 warns about, not observability.
    """
    lines, reds = [], []
    today_name = WEEKDAYS[now_local.weekday()]
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    for name, contract in sorted(clock.artifacts.items()):
        if contract.due_weekday != today_name:
            continue
        if now_local.date() < contract.active_from:
            lines.append(
                f"- [PENDING] {name} report: assertion active from {contract.active_from}"
            )
            continue
        pattern = pathlib.PurePath(contract.glob).name
        found = sorted(
            p for p in reports_dir.glob("*") if fnmatch.fnmatch(p.name, pattern)
        )
        if not found:
            lines.append(f"- [RED] {name} report: absent (due {today_name})")
            reds.append(
                f"{name} report absent — due {today_name}, nothing matches {contract.glob}"
            )
            continue
        newest = max(found, key=lambda p: p.stat().st_mtime)
        written = datetime.datetime.fromtimestamp(newest.stat().st_mtime, tz=LEAGUE_TZ)
        if written < midnight:
            lines.append(
                f"- [RED] {name} report: {newest.name} stale (written {written:%Y-%m-%d %H:%M})"
            )
            reds.append(
                f"{name} report stale — newest is {newest.name} written "
                f"{written:%Y-%m-%d %H:%M}, before today's deadline"
            )
        else:
            lines.append(f"- [OK] {name} report: {newest.name} ({written:%H:%M})")
    return lines, reds


def archive_continuity_lines(conn, today_local):
    """(render_lines, red_flags) for the P2 Sleeper trending archive.

    A gap in this table cannot be backfilled from any source (R8), so every
    missing day is listed by date rather than summarized as a count. One row
    per direction per day is a complete day: the 100-player server cap lives
    inside `payload`, and Task 1's sanity gate is what polices it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT min(archive_date), count(DISTINCT archive_date) FROM raw.sleeper_trending"
        )
        first_day, distinct_days = cur.fetchone()
    if first_day is None:
        return (
            ["- [RED] trending archive: no rows at all"],
            ["raw.sleeper_trending has no rows — the P2 archive is not running"],
        )
    expected = (today_local - first_day).days + 1
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d::date FROM generate_series(%s::date, %s::date, interval '1 day') d
               WHERE NOT EXISTS (
                   SELECT 1 FROM raw.sleeper_trending t WHERE t.archive_date = d::date)
               ORDER BY d""",
            (first_day, today_local),
        )
        gaps = [r[0].isoformat() for r in cur.fetchall()]
    if gaps:
        return (
            [
                f"- [RED] trending archive: {distinct_days}/{expected} days since {first_day} "
                f"— {len(gaps)} gap(s): {', '.join(gaps)}"
            ],
            [f"trending archive gaps (unrecoverable): {', '.join(gaps)}"],
        )
    return (
        [f"- [OK] trending archive: {distinct_days}/{expected} days since {first_day}"],
        [],
    )


def expected_season_lines(conn, clock, states=None):
    """(render_lines, red_flags): is each nflverse feed loaded for the season
    its contract says it should be? A feed that is fresh but pointed at last
    season is R1 in a different costume.

    `states` is health_lines()' source -> SourceState map. A source that is
    BROKEN or KNOWN-LAGGING never gets an [OK] season line here: two lines
    about the same source disagreeing is how a stale feed gets read as fine.
    """
    lines, reds = [], []
    states = states or {}
    for source, table in (
        ("nflverse_player_week", "raw.nflverse_player_week"),
        ("nflverse_snap_counts", "raw.nflverse_snap_counts"),
    ):
        expected = clock.contract(source).expected_season
        if expected is None:
            continue
        with conn.cursor() as cur:
            # raw.nflverse_snap_counts does not exist until Task 7. Probing
            # first keeps a missing table a RED LINE rather than an
            # UndefinedTable that aborts the transaction and kills the whole
            # briefing — a crashed briefing is a silent one.
            cur.execute("SELECT to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                lines.append(f"- [RED] {source}: table {table} does not exist")
                reds.append(f"{source}: table {table} does not exist")
                continue
            cur.execute(
                f"SELECT max(season), max(week) FROM {table} WHERE season = %s",
                (expected,),
            )
            season, week = cur.fetchone()
        if season is None:
            lines.append(f"- [RED] {source}: no rows for expected season {expected}")
            reds.append(f"{source} has no rows for expected_season {expected}")
        else:
            st = states.get(source, health.SourceState.OK)
            lines.append(
                f"- [{mark(st)}] {source}: season {season} through week {week}"
            )
    return lines, reds
