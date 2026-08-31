"""Derive public.usage_weekly from the nflverse feeds (ADR Domain 2).

The partial-publish guard (R5) is the load-bearing part. nflverse publishes
incrementally: a team-week can appear with three players in it. Dividing by
that short denominator puts one receiver at 100% target share and fires a
false ASCENDING for an entire backfield. This module refuses instead: shares
become NULL, `games_complete` is 0, and a structlog line records the
refusal. A NULL that says "unknown" is worth more than a number that says
"100%" and means "we caught nflverse mid-write".

Week coverage: raw.nflverse_player_week carries the postseason (through week
22); raw.nflverse_snap_counts is REG-only (through week 18). The snap lookup
is therefore a left join in spirit — for weeks 19+ every snap_share is NULL
while target_share / carry_share compute normally. That mismatch is a known
feed boundary, not a partial publish, so it does not touch games_complete.
It does move snap_share out of the frame's available_metrics and into
disabled_metrics, so every rule that `requires` it is disabled by name
instead of quietly evaluating an all-NULL column.
"""

import dataclasses

import structlog

from ffi.usage import METRICS, UsageFrame, UsageRow

log = structlog.get_logger()

# The floor cannot be a play count alone: over raw.nflverse_player_week
# 2019-2025 (3,762 team-games) the minimum is 28 and p1 is 40 — 37 fully
# published team-games (rest weeks, blowouts) sit under 40. Player count is
# the clean discriminator: the thinnest real team-game has 25 player rows,
# a mid-publish fragment has single digits. Require BOTH to trip.
MIN_TEAM_PLAYS = 40
MIN_TEAM_PLAYERS = 20

# Plan 1 has no participation feed (nflverse coverage ends 2023) and no pbp
# feed, so these two are structurally unavailable. Declared here, not
# inferred from NULLs, so the disabling is a decision the briefing can name.
UNAVAILABLE_METRICS = ("route_share", "rz_touches")

# The last regular-season week; raw.nflverse_snap_counts stops here.
LAST_SNAP_WEEK = 18

_STATS_QUERY = """
    SELECT gsis_id, team, position,
           coalesce(targets, 0) AS targets,
           coalesce(carries, 0) AS carries
    FROM raw.nflverse_player_week
    WHERE season = %s AND week = %s AND team IS NOT NULL
"""
_SNAPS_QUERY = """
    SELECT gsis_id, offense_pct
    FROM raw.nflverse_snap_counts
    WHERE season = %s AND week = %s
"""
# load_usage_weekly reconstitutes rows positionally (UsageRow(*r)), so a field
# reorder in ffi.usage would silently shift values between columns — a
# teams_observed landing in games_complete reads as valid data. Derive the
# SELECT list from the dataclass itself, and assert the declared order below
# so that the equally positional _STORE_QUERY is caught by the same tripwire.
_LOAD_COLUMNS = tuple(f.name for f in dataclasses.fields(UsageRow))
_EXPECTED_COLUMNS = (
    "gsis_id",
    "season",
    "week",
    "team",
    "position",
    "snap_share",
    "target_share",
    "carry_share",
    "route_share",
    "rz_touches",
    "team_targets",
    "team_carries",
    "games_complete",
    "teams_observed",
)
if _LOAD_COLUMNS != _EXPECTED_COLUMNS:
    raise ImportError(
        "ffi.usage.UsageRow field order changed to "
        f"{_LOAD_COLUMNS}; the positional VALUES list in _STORE_QUERY is now "
        "stale. Update _EXPECTED_COLUMNS and _STORE_QUERY together."
    )
_LOAD_QUERY = f"""
    SELECT {", ".join(_LOAD_COLUMNS)}
    FROM public.usage_weekly
    WHERE season = %s AND week = %s
    ORDER BY gsis_id
"""
_STORE_QUERY = """
    INSERT INTO public.usage_weekly
        (gsis_id, season, week, team, position, snap_share, target_share,
         carry_share, route_share, rz_touches, team_targets, team_carries,
         games_complete, teams_observed, computed_at)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
    ON CONFLICT (gsis_id, season, week) DO UPDATE SET
        team=EXCLUDED.team, position=EXCLUDED.position,
        snap_share=EXCLUDED.snap_share,
        target_share=EXCLUDED.target_share,
        carry_share=EXCLUDED.carry_share,
        route_share=EXCLUDED.route_share,
        rz_touches=EXCLUDED.rz_touches,
        team_targets=EXCLUDED.team_targets,
        team_carries=EXCLUDED.team_carries,
        games_complete=EXCLUDED.games_complete,
        teams_observed=EXCLUDED.teams_observed,
        computed_at=now()
"""


def _available_metrics() -> frozenset[str]:
    return frozenset(m for m in METRICS if m not in UNAVAILABLE_METRICS)


def _share(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else numerator / denominator


def build_usage_weekly(conn, season: int, week: int) -> UsageFrame:
    with conn.cursor() as cur:
        cur.execute(_STATS_QUERY, (season, week))
        stats = cur.fetchall()
        cur.execute(_SNAPS_QUERY, (season, week))
        snaps = {gsis_id: pct for gsis_id, pct in cur.fetchall()}
    if not stats:
        raise ValueError(
            f"build_usage_weekly: raw.nflverse_player_week has no rows for "
            f"season {season} week {week} — run scripts/ingest_nflverse.py"
        )

    team_targets: dict[str, int] = {}
    team_carries: dict[str, int] = {}
    team_players: dict[str, int] = {}
    for _, team, _, targets, carries in stats:
        team_targets[team] = team_targets.get(team, 0) + targets
        team_carries[team] = team_carries.get(team, 0) + carries
        team_players[team] = team_players.get(team, 0) + 1
    teams_observed = len(team_targets)

    incomplete = {
        team
        for team in team_targets
        if team_targets[team] + team_carries[team] < MIN_TEAM_PLAYS
        and team_players[team] < MIN_TEAM_PLAYERS
    }
    if incomplete:
        log.warning(
            "usage.partial_team_weeks",
            season=season,
            week=week,
            teams=sorted(incomplete),
            plays={t: team_targets[t] + team_carries[t] for t in sorted(incomplete)},
            players={t: team_players[t] for t in sorted(incomplete)},
            floor_plays=MIN_TEAM_PLAYS,
            floor_players=MIN_TEAM_PLAYERS,
            note="share metrics refused (NULL) for these teams — R5 partial publish",
        )

    available = set(_available_metrics())
    disabled = list(UNAVAILABLE_METRICS)
    if not snaps:
        # Weeks 19+ have no snap feed by construction; inside the regular
        # season an empty snap map means the feed is behind. Either way the
        # metric is gone for this week, so it leaves available_metrics — the
        # frame, not just the log, has to say so, or a snap rule downstream
        # runs over an all-NULL column and reads the absence as a signal
        # (R27: degrade by removal, never by silent null-handling).
        reason = (
            "postseason — snap_counts is REG-only (weeks 1-18)"
            if week > LAST_SNAP_WEEK
            else "snap feed missing for a regular-season week — snap_share NULL"
        )
        available.discard("snap_share")
        disabled.append("snap_share")
        log.warning("usage.no_snap_counts", season=season, week=week, note=reason)

    rows = []
    for gsis_id, team, position, targets, carries in stats:
        complete = 0 if team in incomplete else 1
        rows.append(
            UsageRow(
                gsis_id=gsis_id,
                season=season,
                week=week,
                team=team,
                position=position,
                # Published as a share by nflverse, not derived from a
                # denominator this module computes, so the partial-publish
                # guard does not gate it. `.get(...) is None` rather than
                # `gsis_id not in snaps`: offense_pct is nullable, so a
                # present-but-NULL row would reach float(None) and crash the
                # whole week's build. Reverting to `not in` breaks
                # test_null_offense_pct_yields_null_snap_share.
                snap_share=(
                    None if snaps.get(gsis_id) is None else float(snaps[gsis_id])
                ),
                target_share=(
                    _share(targets, team_targets[team]) if complete else None
                ),
                carry_share=(_share(carries, team_carries[team]) if complete else None),
                route_share=None,
                rz_touches=None,
                team_targets=team_targets[team],
                team_carries=team_carries[team],
                games_complete=complete,
                teams_observed=teams_observed,
            )
        )

    return UsageFrame(
        season=season,
        week=week,
        rows=tuple(rows),
        available_metrics=frozenset(available),
        disabled_metrics=tuple(disabled),
        teams_observed=teams_observed,
    )


def store_usage_weekly(conn, frame: UsageFrame) -> int:
    """Upsert the frame. Returns the row count written."""
    with conn.cursor() as cur:
        for r in frame.rows:
            cur.execute(
                _STORE_QUERY,
                (
                    r.gsis_id,
                    r.season,
                    r.week,
                    r.team,
                    r.position,
                    r.snap_share,
                    r.target_share,
                    r.carry_share,
                    r.route_share,
                    r.rz_touches,
                    r.team_targets,
                    r.team_carries,
                    r.games_complete,
                    r.teams_observed,
                ),
            )
    conn.commit()
    return len(frame.rows)


def load_usage_weekly(conn, season: int, week: int) -> UsageFrame:
    with conn.cursor() as cur:
        cur.execute(_LOAD_QUERY, (season, week))
        rows = tuple(UsageRow(*r) for r in cur.fetchall())
    if not rows:
        raise ValueError(
            f"load_usage_weekly: public.usage_weekly has no rows for season "
            f"{season} week {week} — run build_usage_weekly + store_usage_weekly"
        )
    available = set(_available_metrics())
    disabled = list(UNAVAILABLE_METRICS)
    if all(r.snap_share is None for r in rows):
        # The same removal build_usage_weekly makes, restated on the load
        # path: a stored postseason week is all-NULL snap_share, and a frame
        # that still advertises snap_share would let a snap rule run over it.
        available.discard("snap_share")
        disabled.append("snap_share")
    return UsageFrame(
        season=season,
        week=week,
        rows=rows,
        available_metrics=frozenset(available),
        disabled_metrics=tuple(disabled),
        teams_observed=rows[0].teams_observed,
    )
