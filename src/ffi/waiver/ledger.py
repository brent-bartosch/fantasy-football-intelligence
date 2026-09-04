"""Event-sourced move-budget ledger (ADR R21, Domain 8).

The weekly acquisition cap (5 NAJEE / 6 LMU) is a scarce resource, and the
only honest count of "moves used this week" is the event log — not a running
counter that can drift from a missed commit. This module reads
`public.waiver_move_events` (written by the league-state adapter) and applies a
conservative safety margin so the operator never spends a move that a late
manual capture will later reveal was already gone.

Pure core (`remaining`, `diff`) + thin DB wrappers. Postgres via `ffi.db`.
"""
from __future__ import annotations

import datetime
from collections.abc import Mapping

from ffi import db
from ffi.league_profile import LeagueProfile, get_profile
from ffi.league_state.clock import LeagueClock, load as load_clock
from ffi.waiver import LedgerDiff

# Keep one move in reserve. A capture that lands after the Monday report can
# only shrink the remaining count, never grow it — so a report must never say
# "5 left" when the true number is 4 and one more capture is still incoming.
SAFETY_MARGIN = 1


def remaining(
    weekly_limit: int, moves_used: int, safety_margin: int = SAFETY_MARGIN
) -> int:
    """Conservative moves remaining, floored at zero."""
    if weekly_limit < 0 or moves_used < 0 or safety_margin < 0:
        raise ValueError(
            f"remaining: limits must be >= 0 (limit={weekly_limit}, "
            f"used={moves_used}, margin={safety_margin})"
        )
    return max(0, weekly_limit - moves_used - safety_margin)


def diff(observed: Mapping[int, int], ledger: Mapping[int, int]) -> LedgerDiff:
    """Diff observed totals against the ledger's own count, team by team."""
    teams = sorted(set(observed) | set(ledger))
    diffs = []
    for team in teams:
        l = ledger.get(team, 0)
        o = observed.get(team, 0)
        if l != o:
            diffs.append((team, l, o))
    return LedgerDiff(diffs=tuple(diffs), balanced=not diffs)


def _week_for(clock: LeagueClock, as_of) -> int:
    d = as_of if isinstance(as_of, datetime.date) else as_of.date()
    for week in range(1, 20):
        start = clock.week_start(week).date()
        end = clock.week_start(week + 1).date()
        if start <= d < end:
            return week
    raise ValueError(f"as_of {d} is outside the regular-season window (weeks 1-19)")


def _count_moves(conn, league_id, week, team_id) -> int:
    conn, close = (conn, False) if conn is not None else (db.connect(), True)
    try:
        with conn.cursor() as cur:
            if league_id is None:
                cur.execute(
                    "SELECT count(*) FROM public.waiver_move_events "
                    "WHERE team_id=%s AND week=%s",
                    (team_id, week),
                )
            else:
                cur.execute(
                    "SELECT count(*) FROM public.waiver_move_events "
                    "WHERE team_id=%s AND week=%s AND league_id=%s",
                    (team_id, week, league_id),
                )
            return int(cur.fetchone()[0])
    finally:
        if close:
            conn.close()


def moves_remaining(
    team_id: int,
    as_of,
    conn=None,
    profile: LeagueProfile | None = None,
    safety_margin: int = SAFETY_MARGIN,
) -> int:
    """Conservative moves remaining for a team as-of a date.

    The weekly cap is read from the league profile (5 NAJEE / 6 LMU), never
    hardcoded, and the ledger is scoped to that profile's league id.
    """
    profile = profile or get_profile("najee")
    clock = load_clock()
    week = _week_for(clock, as_of)
    used = _count_moves(conn, profile.league_id, week, team_id)
    return remaining(profile.weekly_acquisitions, used, safety_margin)


def _ledger_totals(conn, league_id, week) -> dict[int, int]:
    conn, close = (conn, False) if conn is not None else (db.connect(), True)
    try:
        with conn.cursor() as cur:
            if league_id is None:
                cur.execute(
                    "SELECT team_id, count(*) FROM public.waiver_move_events "
                    "WHERE week=%s GROUP BY team_id",
                    (week,),
                )
            else:
                cur.execute(
                    "SELECT team_id, count(*) FROM public.waiver_move_events "
                    "WHERE week=%s AND league_id=%s GROUP BY team_id",
                    (week, league_id),
                )
            return {int(t): int(c) for t, c in cur.fetchall()}
    finally:
        if close:
            conn.close()


def reconcile(
    observed_totals: Mapping[int, int],
    conn=None,
    league_id: int | None = None,
    week: int | None = None,
) -> LedgerDiff:
    """Reconcile observed per-team move totals against the event ledger."""
    if week is None:
        raise ValueError("reconcile: week is required (the ledger is weekly)")
    ledger = _ledger_totals(conn, league_id, week)
    return diff(observed_totals, ledger)
