"""League-profile registry: the single source of truth for league-shape knobs.

A league profile bundles everything that differs between the NAJEE (12-team,
2-QB) league and any other league (e.g. LMU: 14-team, 1-QB) — scoring config
version, team count, rounds, starters, bench/IR, valuation scenario, and the
Sleeper ADP field + sanity gate that match the league's QB demand.

Leaf module (ARCHITECTURE.md Layer 0): imports nothing internal, so any
`src/ffi/*` module may read it without creating a cycle. The strategy knobs
(`StrategyParams`) deliberately live in `ffi.sim.strategy`, NOT here — strategy
is a Layer-2 concern and must not be pulled down into the leaf layer.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

# Position vocabulary shared across the whole stack (also in ffi.sim.priors /
# ffi.sim.opponent; kept here too so a profile is self-describing).
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass(frozen=True)
class LeagueProfile:
    name: str
    config_version: int  # scoring.config version (v1=NAJEE, v2=LMU)
    teams: int
    rounds: int
    starters: dict  # {"QB": 2, ...} vs {"QB": 1, ...}
    bench: int
    ir: int
    scenario: str  # valuation.player_value scenario name
    adp_field: str  # Sleeper stats field: 'adp_2qb' vs 'adp_std'
    qb_sanity_min: int  # min QBs in ADP top-30 (2-QB gate vs 1-QB gate)
    league_id: int  # Yahoo league id (326814 vs 878667)
    weekly_acquisitions: int  # weekly add/drop cap (5 vs 6)
    season_acquisitions: int | None  # season cap; None = unlimited (LMU)
    trade_cutoff: datetime.date  # last day a trade can be accepted

    # NOTE: this is deliberately named `trade_cutoff`, not `trade_deadline`.
    # scripts/validate_league_clock.py treats `trade_deadline` as an UNSET/
    # UNVERIFIED league-clock field and fails any src/scripts module that names
    # it; the per-league cutoff here is a committed decision, distinct from the
    # (unverified) NAJEE league_rules.md transcription.

    @property
    def total_roster(self) -> int:
        starters = sum(self.starters.values()) + 1  # +1 FLEX (W/R/T)
        return starters + self.bench + self.ir

    @property
    def total_picks(self) -> int:
        return self.teams * self.rounds


NAJEE = LeagueProfile(
    name="najee",
    config_version=1,
    teams=12,
    rounds=19,
    starters={"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1},
    bench=8,
    ir=1,
    scenario="qb_hoard_12",
    adp_field="adp_2qb",
    qb_sanity_min=8,
    league_id=326814,
    weekly_acquisitions=5,
    season_acquisitions=65,
    trade_cutoff=datetime.date(2026, 11, 22),
)

LMU = LeagueProfile(
    name="lmu",
    config_version=2,
    teams=14,
    rounds=18,
    starters={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1},
    bench=6,
    ir=2,
    scenario="lmu_1qb_14",
    adp_field="adp_std",
    qb_sanity_min=2,
    league_id=878667,
    weekly_acquisitions=6,
    season_acquisitions=None,
    trade_cutoff=datetime.date(2026, 11, 28),
)

PROFILES = {
    NAJEE.name: NAJEE,
    LMU.name: LMU,
}


def get_profile(name: str) -> LeagueProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"unknown league profile {name!r} (known: {sorted(PROFILES)})"
        ) from None
