"""Monday-evening claims brief -> `reports/claims-YYYY-WW.md`.

The claims brief tells the operator, before the Tuesday waiver run, what to
claim and what it costs. Its composites (priority decisions, move budget) are
gated inputs: if any gated source is BROKEN, the whole brief renders
`NO SIGNAL — ...` via `ffi.health.render_or_refuse` instead of computing on a
failed input (ADR §3a).

Layer 4 (ARCHITECTURE §2): renders precomputed state, never runs an ingest or
simulation. Postgres via `ffi.db`, flags via `ffi.flags`.
"""
from __future__ import annotations

from ffi import flags, health
from ffi.league_profile import LeagueProfile, get_profile
from ffi.league_state.clock import deadline, load as load_clock

GATED_SOURCES = ("nflverse_player_week", "sleeper_trending")


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
        lines.append(
            f"- Waiver advisor: OFF (disabled {flags.disabled_since('waiver_advisor')})"
        )
    if conn is None:
        lines += ["", "_(no roster data — run with a connection for the move budget)_"]
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
