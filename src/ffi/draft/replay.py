"""Pure draft-state derivation from an ordered event stream (Phase 4 / Task 13).

This is the deterministic half of the assistant: given the pick/undo events
(replayed from a `DraftLog` or streamed live from the poller), reconstruct the
board-relevant state -- who has been taken, per-seat position counts, the
draft-order (position -> franchise slot) map derived from round 1, and whose
turn it is. `DraftSession` (session.py) owns the I/O (polling, logging, the
mode machine); everything here is a pure function of its inputs, so Task 16's
async lane can import exactly this to reason about the same board without a
live session.

No try/except, no fallbacks: a draft-order contradiction (our franchise slot
showing up at a draft position that isn't `our_position`) is corruption and
raises immediately (fail-loud), never a silently-patched map.
"""
from dataclasses import dataclass

from ffi.sim.draft import TEAMS, TOTAL_PICKS, snake_position


@dataclass(frozen=True)
class DraftState:
    picks_by_overall: dict  # overall -> pick payload (live; undone picks removed)
    slot_of_position: dict  # draft position (1-12) -> franchise slot, from round 1
    counts_by_position: dict  # draft position -> {pos: count} (resolved picks only)
    taken_refs: frozenset  # refs off the board (crosswalk misses excluded -- see below)
    unresolved: tuple  # overalls whose pick had ref/pos None (crosswalk miss)
    next_overall: int  # the overall pick currently on the clock


def derive_state(events, our_franchise_slot: int, our_position: int) -> DraftState:
    """Fold the ordered `(kind, payload)` event list into a `DraftState`.

    `events` carries only `pick`/`undo` events (meta/mode are the session's
    concern). A `pick` payload has at least `overall`, `pos`, `ref`, and
    (when known) `franchise_slot`; an `undo` payload names the `overall` it
    retracts. Crosswalk misses (`ref`/`pos` None) still consume their overall
    slot -- they are surfaced via `unresolved`, never dropped, so the board's
    pick clock never silently desyncs from Yahoo's.
    """
    picks: dict[int, dict] = {}
    for kind, payload in events:
        if kind == "pick":
            picks[payload["overall"]] = payload
        elif kind == "undo":
            picks.pop(payload["overall"], None)
        else:
            raise ValueError(f"derive_state received a non-pick/undo event: {kind!r}")

    # Draft-order map: round 1 runs draft positions 1..12 ascending, so the
    # round-1 pick at overall o was made by draft position o. Its
    # franchise_slot (when the pick carries one -- polled picks always do)
    # pins position o -> that slot.
    slot_of_position: dict[int, int] = {}
    for overall, p in picks.items():
        rnd, position = snake_position(overall)
        if rnd == 1 and p.get("franchise_slot") is not None:
            slot_of_position[position] = p["franchise_slot"]

    # Cross-check against our known seat: if our franchise slot surfaced at a
    # draft position other than the one the operator declared, the draft order
    # contradicts our configuration -- corruption, not something to paper over.
    for position, slot in slot_of_position.items():
        if slot == our_franchise_slot and position != our_position:
            raise ValueError(
                f"draft-order contradiction: our franchise slot "
                f"{our_franchise_slot} appeared at draft position {position}, "
                f"but our_position was configured as {our_position}"
            )

    counts: dict[int, dict] = {pos: {} for pos in range(1, TEAMS + 1)}
    taken: set[str] = set()
    unresolved: list[int] = []
    for overall, p in picks.items():
        _, position = snake_position(overall)
        ref = p.get("ref")
        pos = p.get("pos")
        if ref is not None and pos is not None:
            taken.add(ref)
            counts[position][pos] = counts[position].get(pos, 0) + 1
        else:
            unresolved.append(overall)

    next_overall = (max(picks) + 1) if picks else 1
    if next_overall > TOTAL_PICKS:
        next_overall = TOTAL_PICKS  # draft complete; clamp rather than run past

    return DraftState(
        picks_by_overall=picks,
        slot_of_position=slot_of_position,
        counts_by_position=counts,
        taken_refs=frozenset(taken),
        unresolved=tuple(sorted(unresolved)),
        next_overall=next_overall,
    )
