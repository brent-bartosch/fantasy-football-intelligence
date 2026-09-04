"""Shared value types for the waiver advisory engine.

This package's `__init__` imports nothing internal, so cutline -> priority ->
clear_time -> ledger -> cards can all reference one set of types with no cycle.
Every decision value carries its reasoning: the operator must be able to trace
a claim/wait/skip verdict back to the numbers that produced it (ADR Domain 5).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

ACTIONS = frozenset({"claim", "wait", "skip"})
AWARDS = frozenset({"refused", "rolling_priority", "fcfs"})
SLOT_TYPES = frozenset({"starter", "bench", "ir"})


@dataclass(frozen=True)
class RosterSlot:
    """One player on a roster, with a value for cutline/priority math."""

    player_id: str
    position: str
    slot_type: str
    value: float

    def __post_init__(self) -> None:
        if self.slot_type not in SLOT_TYPES:
            raise ValueError(f"RosterSlot.slot_type {self.slot_type!r} not in {sorted(SLOT_TYPES)}")


@dataclass(frozen=True)
class CutlineRow:
    """The worst droppable player at a position, from `cutline`."""

    position: str
    worst_droppable: str | None  # player_id, None = no droppable player at all
    worst_value: float | None
    droppable_count: int

    @property
    def has_droppable(self) -> bool:
        return self.worst_droppable is not None


@dataclass(frozen=True)
class Claim:
    """A candidate waiver claim, for `priority.price`."""

    player_id: str
    position: str
    value: float  # 0..1 urgency-adjusted value
    drop_cost: float  # 0..1 value of the player you would drop


@dataclass(frozen=True)
class Decision:
    """A claim / wait / skip verdict with its reasoning."""

    action: str
    reason: str

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"Decision.action {self.action!r} not in {sorted(ACTIONS)}")


@dataclass(frozen=True)
class ClearEvent:
    """A drop -> clear outcome, from `clear_time.next_clear`."""

    clears_at: datetime.datetime | None
    award: str  # 'refused' | 'rolling_priority' | 'fcfs'
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.award not in AWARDS:
            raise ValueError(f"ClearEvent.award {self.award!r} not in {sorted(AWARDS)}")


@dataclass(frozen=True)
class LedgerDiff:
    """The result of reconciling the move-budget ledger against observed totals."""

    diffs: tuple[tuple[int, int, int], ...]  # (team_id, ledger_moves, observed_moves)
    balanced: bool


@dataclass(frozen=True)
class ContingencyCard:
    """A prep-only contingency card ('if X inactive -> add Y')."""

    team_id: int
    starter_player_id: str
    contingency_player_id: str
    position: str
    trigger: str = "inactive"
