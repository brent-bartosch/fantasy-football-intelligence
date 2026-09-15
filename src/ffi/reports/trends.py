"""Tuesday trends & targets report -> `reports/trends-YYYY-WW.md`.

The trends report turns the market archive, the trade-angle engine, and the
usage classifier into a Tuesday read. Its composite (the classification) is
a gated input: a BROKEN usage source renders `NO SIGNAL — ...` via
`ffi.health.render_or_refuse` rather than a confident trend list computed
from a failed feed (ADR §3a).

2026 usage lands when nflverse flips to the 2026 season (2026-09-16 per
config/source_clock.yaml — week 1 publishes ~1 day after MNF); until then
the usage section says so out loud instead of rendering a silently empty
list.

Layer 4 (ARCHITECTURE §2): renders precomputed state, never runs an ingest
or simulation.
"""
from __future__ import annotations

import json

from ffi import flags, health
from ffi.league_profile import get_profile
from ffi.league_state.clock import load as load_clock
from ffi.reports.claims import _market_names, _roster_sleeper_ids
from ffi.trade_angles import Angle, angles as load_angles
from ffi.usage.trends import TrendSignal

MARKET_LIMIT = 10
ANGLE_LIMIT = 40


def _source_state(source: str, conn) -> health.SourceState:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, extract(epoch FROM now() - started_at)/3600 "
            "FROM raw.ingest_runs WHERE source=%s ORDER BY started_at DESC LIMIT 1",
            (source,),
        )
        row = cur.fetchone()
    if row is None:
        return health.state(source, "failed", 1e9)
    return health.state(source, row[0], float(row[1]))


def _latest_market(conn) -> dict[str, tuple[str, list]]:
    """trend_type -> (archive_date, payload rows) for the freshest snapshot
    of each direction."""
    out: dict[str, tuple[str, list]] = {}
    with conn.cursor() as cur:
        for ttype in ("add", "drop"):
            cur.execute(
                "SELECT archive_date, payload FROM raw.sleeper_trending "
                "WHERE trend_type=%s ORDER BY snapshot_id DESC LIMIT 1",
                (ttype,),
            )
            row = cur.fetchone()
            if row is not None:
                payload = row[1] if isinstance(row[1], list) else json.loads(row[1])
                out[ttype] = (str(row[0]), payload)
    return out


def _has_current_usage(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM public.usage_weekly WHERE season=%s LIMIT 1",
            (load_clock().season_first_game_date.year,),
        )
        return cur.fetchone() is not None


def _market_lines(conn, profile) -> list[str]:
    market = _latest_market(conn)
    if not market:
        return ["## Market", "_(no Sleeper snapshots archived yet)_", ""]
    names = _market_names(
        conn, [str(r["player_id"]) for _, rows in market.values() for r in rows]
    )
    taken = _roster_sleeper_ids(conn, profile.league_id)
    lines = []
    for label, ttype in (("adds", "add"), ("drops", "drop")):
        if ttype not in market:
            continue
        archive_date, rows = market[ttype]
        lines.append(f"## Market — top {label} (Sleeper archive {archive_date})")
        for r in rows[:MARKET_LIMIT]:
            sid = str(r["player_id"])
            nm, pos = names.get(sid, ("(unmapped)", "?"))
            mark = " — ON A NAJEE ROSTER" if sid in taken else ""
            lines.append(f"- {r['count']:,} {label} — {nm} ({pos}){mark}")
        lines.append("")
    return lines


def _body(week: int, conn, signals, angles) -> str:
    profile = get_profile("najee")
    clock = load_clock()
    lines = [
        f"# Trends & Targets — Week {week}",
        "",
        f"- League: {profile.name} ({profile.league_id}, {profile.teams} teams)",
        f"- Week window: {clock.week_start(week):%Y-%m-%d} .. "
        f"{clock.week_start(week + 1):%Y-%m-%d}",
    ]
    if not flags.enabled("trade_angles"):
        lines += ["", f"Trade angles: OFF (disabled {flags.disabled_since('trade_angles')})"]
    if not flags.enabled("usage_trends"):
        lines += ["", f"Usage trends: OFF (disabled {flags.disabled_since('usage_trends')})"]

    if conn is not None:
        lines += [""] + _market_lines(conn, profile)
    else:
        lines += ["", "_(market data needs a connection)_"]

    if flags.enabled("trade_angles"):
        lines += ["## Trade angles (freshest roster capture)"]
        if angles:
            # QB repair first — it is the sharpest angle in a 2-QB league.
            ordered = sorted(
                angles,
                key=lambda a: (
                    a.kind != "qb_repair",
                    a.target_team_id or 0,
                    a.position or "",
                ),
            )
            for a in ordered:
                lines.append(f"- [{a.kind}] {a.rationale}")
        else:
            lines.append("_(no angles from the current rosters)_")
        lines.append("")

    if flags.enabled("usage_trends"):
        lines += ["## Usage trends"]
        if signals:
            for s in signals:
                label = "COLD-START" if s.cold_start else s.direction
                lines.append(f"- [{label}] {s.gsis_id} — {s.rule_id}: {s.evidence}")
        elif conn is not None and not _has_current_usage(conn):
            lines.append(
                "2026 usage is not ingested yet — nflverse flips to the 2026 "
                "season on 2026-09-16 (week 1 publishes ~1 day after MNF); "
                "cold-start signals start with next week's report."
            )
        else:
            lines.append("_(no trend signals this week)_")
    return "\n".join(lines) + "\n"


def render_trends(
    week: int,
    conn=None,
    inputs: dict[str, health.SourceState] | None = None,
    signals: list[TrendSignal] | None = None,
    angles: list[Angle] | None = None,
) -> str:
    """Render the Tuesday trends report, gated on the usage-source state.

    `signals`/`angles` may be injected for tests; when `angles` is omitted
    it is loaded from the freshest roster capture via `conn`.
    """
    if inputs is None:
        if conn is None:
            raise ValueError("render_trends: provide conn or an explicit inputs mapping")
        inputs = {"nflverse_player_week": _source_state("nflverse_player_week", conn)}
    if angles is None and conn is not None:
        angles = load_angles(week, limit=ANGLE_LIMIT, conn=conn)
    return health.render_or_refuse(inputs, lambda: _body(week, conn, signals, angles))
