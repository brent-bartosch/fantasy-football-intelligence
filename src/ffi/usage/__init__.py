"""Shared value types for the usage package.

These live in the package __init__ rather than in build.py or trends.py so
that build -> trends -> coldstart can all reference one set of types with no
import cycle. This module imports nothing internal.
"""

from dataclasses import dataclass

# Every metric the trend rules may reference. A rule declares the subset it
# `requires`; a metric absent from a frame's `available_metrics` disables
# every rule that needs it, loudly (R27 — degrade by removal, never by
# silent null-handling).
METRICS = ("snap_share", "target_share", "carry_share", "route_share", "rz_touches")

ASCENDING = "ASCENDING"
FALLING = "FALLING"
WATCH = "WATCH"
DIRECTIONS = (ASCENDING, FALLING, WATCH)


@dataclass(frozen=True)
class UsageRow:
    gsis_id: str
    season: int
    week: int
    team: str
    position: str | None
    snap_share: float | None
    target_share: float | None
    carry_share: float | None
    route_share: float | None
    rz_touches: int | None
    team_targets: int
    team_carries: int
    # 1 when this player's team-week passed the completeness floor, else 0.
    # When 0, every share this builder DERIVES is None (R5).
    games_complete: int
    # Distinct teams present in this (season, week) slate.
    teams_observed: int


@dataclass(frozen=True)
class UsageFrame:
    season: int
    week: int
    rows: tuple[UsageRow, ...]
    available_metrics: frozenset[str]
    disabled_metrics: tuple[str, ...]
    teams_observed: int


@dataclass(frozen=True)
class Rule:
    rule_id: str
    direction: str
    min_weeks: int
    requires: frozenset[str]
    cold_start: bool
    describe: str


@dataclass(frozen=True)
class TrendSignal:
    gsis_id: str
    direction: str
    rule_id: str
    evidence: str
    cold_start: bool


@dataclass(frozen=True)
class TrendResult:
    season: int
    week: int
    signals: tuple[TrendSignal, ...]
    # (rule_id, reason) for every rule that could not run this week.
    disabled_rules: tuple[tuple[str, str], ...]
