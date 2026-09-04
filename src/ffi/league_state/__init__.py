"""League-state value types shared by adapter, manual, api and reconcile.

This package's `__init__` imports nothing internal, so the four modules can
all reference one set of types with no import cycle. `source` and
`ts_precision` live on every row because "success at the wrong precision"
(R17) must be visible to any reader, not just the writer.

Two backends, one output schema (ARCHITECTURE §1b):
  - manual.py is the PRIMARY path (Yahoo API is dead — 403) and the only
    reader of `data/captures/`.
  - api.py is a Yahoo backfill optimization that is never on a critical path.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

# Transaction kinds in the canonical output schema.
KINDS = frozenset({"add", "drop", "add_drop", "trade", "commish"})
# Roster slot types.
SLOT_TYPES = frozenset({"starter", "bench", "ir"})
# Where a row came from.
SOURCES = frozenset({"manual", "yahoo"})
# How precisely `ts` is known. A reader must refuse to render deadline math
# on `unknown` (fail-closed), and must label `date` precision rather than
# passing it off as an exact time.
TS_PRECISIONS = frozenset({"exact", "date", "unknown"})


def _check(member: str, allowed: frozenset[str], where: str) -> str:
    if member not in allowed:
        raise ValueError(
            f"{where}: {member!r} not in {sorted(allowed)} — an unknown value is "
            f"schema drift, not a parse bug"
        )
    return member


@dataclass(frozen=True)
class Transaction:
    """One league transaction in the canonical output schema."""

    league_id: int
    season: int
    week: int | None
    kind: str
    source_transaction_id: str
    ts: datetime.datetime | None = None
    team_id: int | None = None
    payload: dict = field(default_factory=dict)
    source: str | None = None
    ts_precision: str | None = None

    def __post_init__(self) -> None:
        _check(self.kind, KINDS, "Transaction.kind")
        if self.ts_precision is not None:
            _check(self.ts_precision, TS_PRECISIONS, "Transaction.ts_precision")
        if self.source is not None:
            _check(self.source, SOURCES, "Transaction.source")
        if self.ts is not None and self.ts.tzinfo is None:
            raise ValueError("Transaction.ts must be tz-aware (ADR §7 timezones)")
        if not self.source_transaction_id:
            raise ValueError("Transaction.source_transaction_id must be non-empty")


@dataclass(frozen=True)
class RosterRow:
    """One roster slot in the canonical output schema."""

    league_id: int
    season: int
    as_of: datetime.date
    team_id: int
    player_id: str
    slot_type: str
    position: str | None = None
    source: str | None = None
    ts_precision: str | None = None

    def __post_init__(self) -> None:
        _check(self.slot_type, SLOT_TYPES, "RosterRow.slot_type")
        if self.ts_precision is not None:
            _check(self.ts_precision, TS_PRECISIONS, "RosterRow.ts_precision")
        if self.source is not None:
            _check(self.source, SOURCES, "RosterRow.source")
        if not self.player_id:
            raise ValueError("RosterRow.player_id must be non-empty")
