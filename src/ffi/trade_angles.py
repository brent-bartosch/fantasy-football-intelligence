"""Trade angles (ADR Window 6): opponent needs, buy-low / sell-high, QB repair.

Recommend-only — this module NEVER sends a trade offer or a push; it produces
`Angle` values the reports layer renders. The trade deadline is read from the
league profile (multi-league), not from the unverified league-clock field.

Layer 3 (ARCHITECTURE §2): may import db, ids, health, flags, usage,
league_state, waiver, valuation. The roster-derived angles (opponent needs and
QB repair) come from the canonical roster table via the league-state adapter.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from dataclasses import dataclass

from ffi import db
from ffi.league_profile import LeagueProfile, get_profile
from ffi.league_state import adapter
from ffi.league_state.clock import load as load_clock
from ffi.usage import ASCENDING, FALLING, TrendSignal
from ffi.usage.market import MarketState

# A "hot" market for a sell-high angle: net adds at/above this many.
NET_HOT = 5

# The positions worth a TRADE angle. K/DEF are streaming spots — nobody
# trades for kicker depth, and flagging every team that carries exactly one
# kicker (i.e., all of them) is noise, not signal.
TRADE_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True)
class Angle:
    kind: str  # 'opponent_need' | 'buy_low' | 'sell_high' | 'qb_repair'
    position: str | None
    player_id: str | None  # None for position-level needs
    target_team_id: int | None
    rationale: str


def _roster_counts(rosters) -> dict[int, dict[str, int]]:
    by_team: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rosters:
        by_team[r.team_id][r.position] += 1
    return by_team


def opponent_needs(rosters, profile: LeagueProfile) -> list[Angle]:
    """Teams thin at a position (fewer than starters + one depth player)."""
    by_team = _roster_counts(rosters)
    out: list[Angle] = []
    for team in sorted(by_team):
        for pos in TRADE_POSITIONS:
            need = profile.starters.get(pos, 0) + 1  # +1 bench depth
            have = by_team[team].get(pos, 0)
            if have < need:
                out.append(
                    Angle(
                        kind="opponent_need",
                        position=pos,
                        player_id=None,
                        target_team_id=team,
                        rationale=(
                            f"team {team} rosters {have} {pos} "
                            f"(thin vs {need} starters+depth)"
                        ),
                    )
                )
    return out


def qb_repair(rosters, profile: LeagueProfile) -> list[Angle]:
    """Teams rostering fewer QBs than the league starts (2-QB leagues are the
    sharp edge: a team with one QB has no waiver/bye cover)."""
    qb_needed = profile.starters.get("QB", 1)
    qb_counts = defaultdict(int)
    teams = set()
    for r in rosters:
        teams.add(r.team_id)
        if r.position == "QB":
            qb_counts[r.team_id] += 1
    out: list[Angle] = []
    for team in sorted(teams):
        n = qb_counts.get(team, 0)
        if n < qb_needed:
            out.append(
                Angle(
                    kind="qb_repair",
                    position="QB",
                    player_id=None,
                    target_team_id=team,
                    rationale=(
                        f"team {team} rosters {n} QB (needs {qb_needed} "
                        f"in a {qb_needed}-QB league)"
                    ),
                )
            )
    return out


def buy_low_sell_high(
    signals: Sequence[TrendSignal],
    market_states: Mapping[str, MarketState],
) -> list[Angle]:
    """Market-vs-usage divergence: buy a rising player the market hasn't
    noticed, sell a falling player the market is still chasing."""
    out: list[Angle] = []
    for s in signals:
        m = market_states.get(s.gsis_id)
        if m is None or m.gate_failed:
            continue
        net = m.add_count - m.drop_count
        if s.direction == ASCENDING and net <= 0:
            out.append(
                Angle(
                    kind="buy_low",
                    position=None,
                    player_id=s.gsis_id,
                    target_team_id=None,
                    rationale=(
                        f"{s.rule_id}: {s.evidence}; market cold "
                        f"({m.add_count} adds / {m.drop_count} drops)"
                    ),
                )
            )
        elif s.direction == FALLING and net >= NET_HOT:
            out.append(
                Angle(
                    kind="sell_high",
                    position=None,
                    player_id=s.gsis_id,
                    target_team_id=None,
                    rationale=(
                        f"{s.rule_id}: {s.evidence}; market hot "
                        f"({m.add_count} adds / {m.drop_count} drops)"
                    ),
                )
            )
    return out


def angles(
    week: int,
    limit: int = 3,
    conn=None,
    profile: LeagueProfile | None = None,
) -> list[Angle]:
    """Roster-derived trade angles for a week (opponent needs + QB repair).

    Reads the FRESHEST captured roster snapshot for the league — captures
    land mid-week (the operator pastes rosters when convenient), so pinning
    the read to the week-start date would miss them. `week` still drives
    the season derivation, keeping the contract signature intact.

    The market-vs-usage angles (`buy_low_sell_high`) need the usage + market
    pipeline and are exposed separately for the reports layer to combine; the
    roster angles here are computed straight from the canonical roster table.
    """
    profile = profile or get_profile("najee")
    own = conn is None
    if own:
        conn = db.connect()
    try:
        season = load_clock().week_start(week).year
        with conn.cursor() as cur:
            cur.execute(
                "SELECT max(as_of) FROM public.league_rosters "
                "WHERE league_id=%s AND season=%s",
                (profile.league_id, season),
            )
            as_of = cur.fetchone()[0]
        if as_of is None:
            return []  # no roster capture yet — no angles, honestly empty
        rosters = adapter.load_rosters(as_of, conn=conn, league_id=profile.league_id)
    finally:
        if own:
            conn.close()
    out = opponent_needs(rosters, profile) + qb_repair(rosters, profile)
    out.sort(key=lambda a: (a.kind, a.target_team_id or 0, a.position or ""))
    return out[: max(0, limit)]
