"""Monday-evening claims brief -> `reports/claims-YYYY-WW.md`.

The claims brief tells the operator, before the Tuesday waiver run, what to
claim and what it costs. Its composites (market targets, cutline) are gated
inputs: if any gated source is BROKEN, the whole brief renders `NO SIGNAL —
...` via `ffi.health.render_or_refuse` instead of computing on a failed
input (ADR §3a).

The target list is the freshest archived Sleeper add-list, filtered to
players NOT on any roster in the league (via the crosswalk), so the brief
never recommends a taken player. The cutline is the operator's bench valued
by the morning chain's VORP (scenario from the league profile), worst
droppable first.

Layer 4 (ARCHITECTURE §2): renders precomputed state, never runs an ingest
or simulation. Postgres via `ffi.db`, flags via `ffi.flags`, the cutline
engine via `ffi.waiver`.
"""
from __future__ import annotations

import json

from ffi import flags, health
from ffi.league_profile import LeagueProfile, get_profile
from ffi.league_state.clock import deadline, load as load_clock
from ffi.waiver import RosterSlot
from ffi.waiver.cutline import cutline

GATED_SOURCES = ("nflverse_player_week", "sleeper_trending")
# The operator's team (manager_slot_annotations: league_slot 12 = 'Brent').
MY_TEAM_ID = 12
TARGET_LIMIT = 10
# Crosswalk kicker code -> the league's position vocabulary.
_DISPLAY_POS = {"PK": "K"}
_CUTLINE_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})


def _source_state(source: str, conn) -> health.SourceState:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, extract(epoch FROM now() - started_at)/3600 "
            "FROM raw.ingest_runs WHERE source=%s ORDER BY started_at DESC LIMIT 1",
            (source,),
        )
        row = cur.fetchone()
    if row is None:
        # No run ever recorded -> broken, never "assume fresh" (fail-loud).
        return health.state(source, "failed", 1e9)
    return health.state(source, row[0], float(row[1]))


def _gather_inputs(conn) -> dict[str, health.SourceState]:
    return {s: _source_state(s, conn) for s in GATED_SOURCES}


def _latest_adds(conn) -> tuple[list[tuple[str, int]], str]:
    """(sleeper_id, count) pairs from the freshest archived add list, plus
    its archive date for the render."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT archive_date, payload FROM raw.sleeper_trending "
            "WHERE trend_type='add' ORDER BY snapshot_id DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return [], ""
    archive_date, payload = row
    rows = payload if isinstance(payload, list) else json.loads(payload)
    return [(str(r["player_id"]), int(r["count"])) for r in rows], str(archive_date)


def _roster_sleeper_ids(conn, league_id: int) -> set[str]:
    """Every 2026 roster player expressed as a Sleeper id — rookie rows
    carry sleeper ids directly as player_id; yahoo-id rows map through the
    crosswalk. This is the taken-set the target filter checks against."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT player_id FROM public.league_rosters "
            "WHERE league_id=%s AND season=2026",
            (league_id,),
        )
        ids = [r[0] for r in cur.fetchall()]
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT yahoo_id, sleeper_id FROM public.player_id_xwalk "
            "WHERE yahoo_id = ANY(%s) AND sleeper_id IS NOT NULL",
            (ids,),
        )
        yahoo_to_sleeper = dict(cur.fetchall())
    taken = set(ids)
    taken.update(s for s in (yahoo_to_sleeper.get(i) for i in ids) if s)
    return taken


def _market_names(conn, sleeper_ids: list[str]) -> dict[str, tuple[str, str]]:
    """sleeper_id -> (name, position) for the crosswalk-mapped market players."""
    if not sleeper_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sleeper_id, name, position FROM public.player_id_xwalk "
            "WHERE sleeper_id = ANY(%s)",
            (sleeper_ids,),
        )
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _names_for_ids(conn, ids: list[str]) -> dict[str, str]:
    """Any-namespace id -> player name, via the crosswalk."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT yahoo_id, sleeper_id, gsis_id, name FROM public.player_id_xwalk "
            "WHERE yahoo_id = ANY(%s) OR sleeper_id = ANY(%s) OR gsis_id = ANY(%s)",
            (ids, ids, ids),
        )
        out = {}
        for y, s, g, name in cur.fetchall():
            for v in (y, s, g):
                if v is not None:
                    out[str(v)] = name
        return out


def _bench_values(conn, league_id: int, team_id: int, scenario: str):
    """(player_id, position, vorp | None) for one team's bench. Unvalued
    players carry None and are LISTED in the brief, never dropped silently."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT player_id, position FROM public.league_rosters "
            "WHERE league_id=%s AND season=2026 AND team_id=%s AND slot_type='bench' "
            "ORDER BY player_id",
            (league_id, team_id),
        )
        bench = [(r[0], r[1]) for r in cur.fetchall()]
    if not bench:
        return []
    ids = [pid for pid, _ in bench]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT xwalk_id, yahoo_id, sleeper_id, gsis_id "
            "FROM public.player_id_xwalk "
            "WHERE yahoo_id = ANY(%s) OR sleeper_id = ANY(%s) OR gsis_id = ANY(%s)",
            (ids, ids, ids),
        )
        id_to_xwalk = {}
        for xwalk_id, y, s, g in cur.fetchall():
            for v in (y, s, g):
                if v is not None:
                    id_to_xwalk[str(v)] = xwalk_id
        xwalk_ids = [id_to_xwalk[pid] for pid, _ in bench if pid in id_to_xwalk]
        xwalk_to_vorp = {}
        if xwalk_ids:
            cur.execute(
                "SELECT DISTINCT ON (xwalk_id) xwalk_id, vorp "
                "FROM valuation.player_value WHERE scenario=%s AND xwalk_id = ANY(%s) "
                "ORDER BY xwalk_id, computed_at DESC",
                (scenario, xwalk_ids),
            )
            xwalk_to_vorp = {r[0]: float(r[1]) for r in cur.fetchall()}
    out = []
    for pid, pos in bench:
        xw = id_to_xwalk.get(pid)
        out.append((pid, pos, xwalk_to_vorp.get(xw) if xw is not None else None))
    return out


def _display(position: str) -> str:
    return _DISPLAY_POS.get(position, position)


def _body(week: int, conn, profile: LeagueProfile) -> str:
    clock = load_clock()
    d = deadline("waivers", week, clock)
    lines = [
        f"# Claims Brief — Week {week}",
        "",
        f"- Waiver deadline: {d:%Y-%m-%d %H:%M %Z}",
        f"- League: {profile.name} ({profile.league_id}, {profile.teams} teams)",
    ]
    if not flags.enabled("waiver_advisor"):
        lines += [
            "",
            f"Waiver advisor: OFF (disabled {flags.disabled_since('waiver_advisor')}) "
            "— targets and cutline suppressed",
        ]
        return "\n".join(lines) + "\n"
    if conn is None:
        lines += ["", "_(no roster data — run with a connection for the move budget)_"]
        return "\n".join(lines) + "\n"

    # --- waiver targets: freshest market adds, filtered to available players
    adds, archive_date = _latest_adds(conn)
    taken = _roster_sleeper_ids(conn, profile.league_id)
    names = _market_names(conn, [sid for sid, _ in adds])
    targets = [(sid, c) for sid, c in adds if sid not in taken and sid in names]
    unmapped = sum(1 for sid, _ in adds if sid not in names)
    lines += [
        "",
        f"## Waiver targets — market adds available in {profile.name.upper()}",
        f"Freshest Sleeper add list: {archive_date or '(none archived)'}",
    ]
    if targets:
        ordered = sorted(targets, key=lambda t: -t[1])[:TARGET_LIMIT]
        lines.append(f"Top {len(ordered)}, by add count:")
        for sid, count in ordered:
            nm, pos = names[sid]
            lines.append(f"- {count:,} adds — {nm} ({_display(pos)})")
    else:
        lines.append("_(no available named targets in the latest add list)_")
    if unmapped:
        lines.append(f"({unmapped} add-list player(s) not in the crosswalk — excluded)")

    # --- cutline: the operator's bench, worst droppable first
    bench = _bench_values(conn, profile.league_id, MY_TEAM_ID, profile.scenario)
    bench_names = _names_for_ids(conn, [pid for pid, _, _ in bench])
    valued = [(pid, pos, v) for pid, pos, v in bench if v is not None]
    unvalued = [pid for pid, _, v in bench if v is None]
    lines += ["", "## Cutline — what an add costs (your bench, worst droppable first)"]
    if valued:
        for pid, pos, v in sorted(valued, key=lambda t: t[2]):
            lines.append(f"- {bench_names.get(pid, pid)} ({_display(pos)}) — vorp {v:.1f}")
        if targets:
            top_sid = max(targets, key=lambda t: t[1])[0]
            top_pos = _display(names[top_sid][1])
            if top_pos in _CUTLINE_POSITIONS:
                slots = [RosterSlot(pid, pos, "bench", v) for pid, pos, v in valued]
                row = cutline(slots, top_pos)
                if row.worst_droppable:
                    lines.append(
                        f"- Adding a {top_pos} displaces: "
                        f"{bench_names.get(row.worst_droppable, row.worst_droppable)} "
                        f"(vorp {row.worst_value:.1f})"
                    )
    else:
        lines.append("_(no valued bench players — valuation data missing)_")
    if unvalued:
        lines.append(
            "(no valuation for: "
            + ", ".join(bench_names.get(p, p) for p in unvalued)
            + ")"
        )
    return "\n".join(lines) + "\n"


def render_claims(
    week: int, conn=None, inputs: dict[str, health.SourceState] | None = None
) -> str:
    """Render the Monday claims brief for a week, gated on the input states.

    `inputs` may be injected for tests; when omitted, they are gathered from
    the ingest-run table via `conn` (which is then required).
    """
    if inputs is None:
        if conn is None:
            raise ValueError("render_claims: provide conn or an explicit inputs mapping")
        inputs = _gather_inputs(conn)
    profile = get_profile("najee")
    return health.render_or_refuse(inputs, lambda: _body(week, conn, profile))
