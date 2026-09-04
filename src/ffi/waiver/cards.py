"""Contingency cards ("if X inactive -> add Y"). Prep only.

Window-3 automation is de-scoped (ADR Domain 3): these cards are prepared, not
acted on. They answer "if my starter is a late scratch, who do I already know
I want?" — the cheapest possible insurance against a Sunday-morning inactive
that would otherwise force a panicked add.

A card is emitted for every starter position with NO bench/IR backup, pairing
that starter (X) with the best available free agent (Y) at the position.
Positions with a bench backup are covered and produce no card.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ffi.waiver import ContingencyCard, RosterSlot


def build_cards(
    team_id: int,
    roster: Sequence[RosterSlot],
    available: Mapping[str, Sequence[str]],
) -> list[ContingencyCard]:
    """Build prep-only contingency cards for a roster.

    `available` maps position -> ranked free-agent player_ids (best first).
    """
    covered_positions = {p.position for p in roster if p.slot_type in ("bench", "ir")}
    cards: list[ContingencyCard] = []
    for starter in roster:
        if starter.slot_type != "starter":
            continue
        if starter.position in covered_positions:
            continue
        fa = available.get(starter.position) or []
        if not fa:
            continue
        cards.append(
            ContingencyCard(
                team_id=team_id,
                starter_player_id=starter.player_id,
                contingency_player_id=fa[0],
                position=starter.position,
            )
        )
    cards.sort(key=lambda c: (c.position, c.starter_player_id))
    return cards
