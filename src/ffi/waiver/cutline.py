"""Position-aware roster cutline (ADR Domain 2).

`cutline` answers the question every waiver claim is really asking: WHO would I
drop to add this free agent, and is the FA worth more than that player? It is
position-aware — adding a QB means cutting the worst droppable QB first, not the
worst bench player at some unrelated position.

Pure function over an already-built roster. Imports only `ffi.waiver` value
types, so it stays below the `waiver` layer's other modules.
"""
from __future__ import annotations

from collections.abc import Sequence

from ffi.waiver import CutlineRow, RosterSlot


def cutline(roster: Sequence[RosterSlot], position: str) -> CutlineRow:
    """The worst droppable player for a claim at `position`.

    Droppable = bench or IR (a starter cannot be dropped without replacing the
    lineup slot). Prefers a same-position drop; falls back to the worst bench/IR
    player of any position when the roster has no same-position droppable.
    """
    if position not in ("QB", "RB", "WR", "TE", "K", "DEF"):
        raise ValueError(
            f"cutline: unknown position {position!r} (expected QB/RB/WR/TE/K/DEF)"
        )
    droppable = [p for p in roster if p.slot_type in ("bench", "ir")]
    if not droppable:
        return CutlineRow(
            position=position,
            worst_droppable=None,
            worst_value=None,
            droppable_count=0,
        )
    same_pos = [p for p in droppable if p.position == position]
    pool = same_pos or droppable
    worst = min(pool, key=lambda p: p.value)
    return CutlineRow(
        position=position,
        worst_droppable=worst.player_id,
        worst_value=worst.value,
        droppable_count=len(droppable),
    )


def worth_adding(fa_value: float, row: CutlineRow) -> bool:
    """Is a free agent worth more than the player it would displace?

    A roster with no droppable player cannot add without a corresponding drop
    the caller hasn't supplied, so it fails closed (False).
    """
    if row.worst_value is None:
        return False
    return fa_value > row.worst_value
