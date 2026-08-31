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
"""

import structlog

from ffi.usage import METRICS, UsageFrame, UsageRow

log = structlog.get_logger()

# A real NFL team-game has ~55-70 (targets + carries). 40 clears every
# legitimate low-volume game (the lowest 2019-2025 team-game is 44) while
# tripping on any mid-publish fragment.
MIN_TEAM_PLAYS = 40

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
_LOAD_QUERY = """
    SELECT gsis_id, season, week, team, position, snap_share, target_share,
           carry_share, route_share, rz_touches, team_targets, team_carries,
           games_complete, teams_observed
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
    for _, team, _, targets, carries in stats:
        team_targets[team] = team_targets.get(team, 0) + targets
        team_carries[team] = team_carries.get(team, 0) + carries
    teams_observed = len(team_targets)

    incomplete = {
        team
        for team in team_targets
        if team_targets[team] + team_carries[team] < MIN_TEAM_PLAYS
    }
    if incomplete:
        log.warning(
            "usage.partial_team_weeks",
            season=season,
            week=week,
            teams=sorted(incomplete),
            floor=MIN_TEAM_PLAYS,
            note="share metrics refused (NULL) for these teams — R5 partial publish",
        )
    if not snaps:
        # Weeks 19+ have no snap feed by construction; inside the regular
        # season an empty snap map means the feed is behind, and that is
        # worth a line in the log rather than a silent column of NULLs.
        log.warning(
            "usage.no_snap_counts",
            season=season,
            week=week,
            note=(
                "postseason — snap_counts is REG-only (weeks 1-18)"
                if week > LAST_SNAP_WEEK
                else "snap feed missing for a regular-season week — snap_share NULL"
            ),
        )

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
                # guard does not gate it.
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
        available_metrics=_available_metrics(),
        disabled_metrics=UNAVAILABLE_METRICS,
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
    return UsageFrame(
        season=season,
        week=week,
        rows=rows,
        available_metrics=_available_metrics(),
        disabled_metrics=UNAVAILABLE_METRICS,
        teams_observed=rows[0].teams_observed,
    )
