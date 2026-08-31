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
# 20 sits deliberately 5 below the observed real minimum (25): the cost of
# refusing a real team-week outweighs the cost of computing a fragment's
# shares, so the floor errs toward computing.
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
# Both queries are generated from the dataclass field order: load reconstitutes
# rows positionally (UsageRow(*r)) and store binds positionally by the same
# tuple, so a field reorder in ffi.usage can no longer shift values between
# columns — the SQL moves with the dataclass. What generation CANNOT catch is
# a field added to UsageRow that has no column in public.usage_weekly (or a
# column dropped from the table); that would surface as an opaque psycopg
# error mid-week. _EXPECTED_COLUMNS pins the set and order so such a change
# fails at import with a message that names the migration it needs.
_LOAD_COLUMNS = tuple(f.name for f in dataclasses.fields(UsageRow))
# The primary key — excluded from the ON CONFLICT SET list.
_KEY_COLUMNS = ("gsis_id", "season", "week")
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
        "ffi.usage.UsageRow fields changed to "
        f"{_LOAD_COLUMNS}; _LOAD_QUERY and _STORE_QUERY regenerate themselves, "
        "but public.usage_weekly does not. Confirm every field above is a "
        "column of that table, then update _EXPECTED_COLUMNS to match."
    )
_LOAD_QUERY = f"""
    SELECT {", ".join(_LOAD_COLUMNS)}
    FROM public.usage_weekly
    WHERE season = %s AND week = %s
    ORDER BY gsis_id
"""
_UPDATE_SET = ", ".join(
    f"{c}=EXCLUDED.{c}" for c in _LOAD_COLUMNS if c not in _KEY_COLUMNS
)
_STORE_QUERY = f"""
    INSERT INTO public.usage_weekly ({", ".join(_LOAD_COLUMNS)}, computed_at)
    VALUES ({", ".join("%s" for _ in _LOAD_COLUMNS)}, now())
    ON CONFLICT ({", ".join(_KEY_COLUMNS)}) DO UPDATE SET
        {_UPDATE_SET}, computed_at=now()
"""


def _metric_sets(
    rows: tuple[UsageRow, ...], season: int, week: int, path: str
) -> tuple[frozenset[str], tuple[str, ...]]:
    """The single snap_share availability decision, shared by build and load.

    The predicate is the assembled rows, never the raw snap map: build and
    load must reach the same verdict from the same evidence, or a
    build -> store -> load round trip silently changes a frame's
    available_metrics. An all-NULL snap_share column leaves available_metrics
    and is named in disabled_metrics, so every rule that `requires` it is
    disabled by name rather than quietly evaluating NULLs (R27 — degrade by
    removal, never by silent null-handling).
    """
    available = {m for m in METRICS if m not in UNAVAILABLE_METRICS}
    disabled = list(UNAVAILABLE_METRICS)
    if all(r.snap_share is None for r in rows):
        available.discard("snap_share")
        disabled.append("snap_share")
        # Weeks 19+ have no snap feed by construction — expected, not news.
        # Inside the regular season the same shape means the feed is behind
        # or broken, which is an outage and has to be said as one.
        postseason = week > LAST_SNAP_WEEK
        emit = log.info if postseason else log.warning
        emit(
            "usage.no_snap_counts",
            season=season,
            week=week,
            path=path,
            note=(
                "postseason — snap_counts is REG-only (weeks 1-18)"
                if postseason
                else "regular-season week with no usable offense_pct — snap "
                "feed missing or behind; snap_share disabled for this week"
            ),
        )
    return frozenset(available), tuple(disabled)


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

    # Decided from the assembled rows, after the loop, so that build and load
    # apply one predicate to one body of evidence (see _metric_sets).
    frame_rows = tuple(rows)
    available, disabled = _metric_sets(frame_rows, season, week, "build")
    return UsageFrame(
        season=season,
        week=week,
        rows=frame_rows,
        available_metrics=available,
        disabled_metrics=disabled,
        teams_observed=teams_observed,
    )


def store_usage_weekly(conn, frame: UsageFrame) -> int:
    """Upsert the frame. Returns the row count written."""
    with conn.cursor() as cur:
        for r in frame.rows:
            # Bound in _LOAD_COLUMNS order, the same order _STORE_QUERY was
            # generated from, so the two cannot drift apart by hand.
            cur.execute(_STORE_QUERY, tuple(getattr(r, c) for c in _LOAD_COLUMNS))
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
    available, disabled = _metric_sets(rows, season, week, "load")
    return UsageFrame(
        season=season,
        week=week,
        rows=rows,
        available_metrics=available,
        disabled_metrics=disabled,
        teams_observed=rows[0].teams_observed,
    )
