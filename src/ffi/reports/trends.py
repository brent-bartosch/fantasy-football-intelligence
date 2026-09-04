"""Tuesday trends & targets report -> `reports/trends-YYYY-WW.md`.

The trends report turns the usage classifier's ASCENDING / FALLING signals and
the trade-angle engine into a Tuesday read. Its composite (the classification)
is a gated input: a BROKEN usage source renders `NO SIGNAL — ...` via
`ffi.health.render_or_refuse` rather than a confident trend list computed from
a failed feed (ADR §3a).

Layer 4 (ARCHITECTURE §2): renders precomputed state, never runs an ingest or
simulation.
"""
from __future__ import annotations

from ffi import flags, health
from ffi.league_profile import get_profile
from ffi.league_state.clock import load as load_clock
from ffi.trade_angles import Angle
from ffi.usage.trends import TrendSignal


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


def _body(
    week: int,
    signals: list[TrendSignal] | None,
    angles: list[Angle] | None,
) -> str:
    profile = get_profile("najee")
    clock = load_clock()
    lines = [
        f"# Trends & Targets — Week {week}",
        "",
        f"- League: {profile.name} ({profile.league_id}, {profile.teams} teams)",
        f"- Week window: {clock.week_start(week):%Y-%m-%d} .. "
        f"{clock.week_start(week + 1):%Y-%m-%d}",
    ]
    if not flags.enabled("usage_trends"):
        lines.append(
            f"- Usage trends: OFF (disabled {flags.disabled_since('usage_trends')})"
        )
    if not flags.enabled("trade_angles"):
        lines.append(
            f"- Trade angles: OFF (disabled {flags.disabled_since('trade_angles')})"
        )
    lines.append("")
    lines.append("## Trending")
    if signals:
        for s in signals:
            label = "COLD-START" if s.cold_start else s.direction
            lines.append(f"- [{label}] {s.gsis_id} — {s.rule_id}: {s.evidence}")
    else:
        lines.append("_(no trend signals this week)_")
    lines.append("")
    lines.append("## Trade angles")
    if angles:
        for a in angles:
            lines.append(f"- [{a.kind}] {a.rationale}")
    else:
        lines.append("_(no trade angles this week)_")
    return "\n".join(lines) + "\n"


def render_trends(
    week: int,
    conn=None,
    inputs: dict[str, health.SourceState] | None = None,
    signals: list[TrendSignal] | None = None,
    angles: list[Angle] | None = None,
) -> str:
    """Render the Tuesday trends report, gated on the usage-source state."""
    if inputs is None:
        if conn is None:
            raise ValueError("render_trends: provide conn or an explicit inputs mapping")
        inputs = {"nflverse_player_week": _source_state("nflverse_player_week", conn)}
    return health.render_or_refuse(inputs, lambda: _body(week, signals, angles))
