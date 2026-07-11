"""Draft assistant recommendation engine (Phase 4 / Task 12): the board +
roster-need + VONA view surfaced to the operator, pinned equal to the
rehearsed sim strategy.

Consistency contract (load-bearing): `recommend(...).primary` is always
exactly `evaluate_rules(...)[0]` -- the same rule cascade `make_strategy_fn`
uses for the sim farm and backtests. This module makes no strategy decision
of its own; it only adds informational context (a scored candidate board,
VONA annotations, and human-readable notes) around that pinned pick. No DB
access, no I/O, no model calls -- a pure function of its arguments,
deterministic given the same inputs (Task 13 consumes `Recommendation`).

`top`/`by_position`: scored via the same `_score`/`_pick_best` tiebreak as
rule 4, over exactly `rule4_candidates` (`ffi.sim.strategy`) -- the identical
gating rule 4 itself applies (feasible, under cap, in window,
`qb_not_before`/`qb_tier_targets`/`defk_round`-gated). When a FORCE rule
(feasibility/qb_deadline/defk) decides `primary`, `top`/`by_position` still
show rule 4's value-only view -- the operator sees both what's forced and
what pure value says, deliberately not the same thing on a forced turn.

`notes`: a "last tier-N POS on the board" line fires per position whose
top-of-board player (`avail_by_pos[pos][0]`, the ADP-consensus best
available -- not necessarily the rule-4 argmax) is alone in its tier among
that position's full available list -- taking it now closes out the tier.
This fires regardless of `tier_break_bonus` (which only reweights `top`'s
ordering, not whether the note fires). VONA lines ("waiting one turn costs
~X vorp at POS") fire when `forecast` is given and that position's
`vona(...)` value is at least `MATERIAL_VONA` -- a judgment-call threshold
(not pinned by the brief) chosen so the operator isn't shown a note for
every position's noise-level VONA.
"""
from dataclasses import dataclass

from ffi.sim.availability import AvailabilityForecast, vona
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import POSITIONS
from ffi.sim.strategy import (
    StrategyParams,
    adp_sort_key,
    evaluate_rules,
    is_last_in_tier,
    rule4_candidates,
)

MATERIAL_VONA = 1.0  # vorp points; below this, a "waiting costs" note is noise


@dataclass(frozen=True)
class Recommendation:
    primary: PoolPlayer
    rule: str  # "feasibility" | "qb_deadline" | "defk" | "value"
    top: tuple  # (score, PoolPlayer) desc, top 8 legal rule-4 candidates
    by_position: dict  # pos -> top 3 PoolPlayer (legal rule-4 candidates)
    vona: dict | None  # pos -> vorp; None when no forecast supplied
    notes: tuple  # human-readable strings


def _sorted_desc(scored: list) -> list:
    """`scored` ranked exactly as `_pick_best` would break its own tie (score
    desc, then lower ADP with None last, then name) -- but keeping every
    entry rather than just the argmax, so `ranked[0]` always agrees with
    what `_pick_best(scored)` would have returned."""

    def key(item):
        score, player = item
        return (-score, adp_sort_key(player), player.name)

    return sorted(scored, key=key)


def _last_in_tier_notes(avail_by_pos: dict) -> list:
    notes = []
    for pos, cands in avail_by_pos.items():
        if not cands:
            continue
        best = cands[0]
        if is_last_in_tier(best, cands):
            notes.append(f"last tier-{best.tier} {pos} on the board")
    return notes


def _vona_notes(vona_by_pos: dict) -> list:
    notes = []
    for pos in POSITIONS:
        v = vona_by_pos.get(pos, 0.0)
        if v >= MATERIAL_VONA:
            notes.append(f"waiting one turn costs ~{v:.1f} vorp at {pos}")
    return notes


def recommend(
    avail_by_pos: dict,
    round_: int,
    counts: dict,
    picks_left_after: int,
    params: StrategyParams,
    forecast: AvailabilityForecast | None = None,
) -> Recommendation:
    """The assistant's full recommendation view for the current turn.

    `primary`/`rule` are exactly `evaluate_rules(...)` -- see the consistency
    contract in this module's docstring. Everything else (`top`,
    `by_position`, `vona`, `notes`) is additive context around that pinned
    pick, never a second opinion that could override it."""
    primary, rule = evaluate_rules(
        avail_by_pos, round_, counts, picks_left_after, params
    )

    scored = rule4_candidates(avail_by_pos, round_, counts, picks_left_after, params)
    ranked = _sorted_desc(scored)
    top = tuple(ranked[:8])

    by_position = {}
    for pos in POSITIONS:
        pos_ranked = [p for _, p in ranked if p.position == pos]
        by_position[pos] = tuple(pos_ranked[:3])

    notes = _last_in_tier_notes(avail_by_pos)

    vona_dict = None
    if forecast is not None:
        vona_dict = vona(avail_by_pos, forecast)
        notes = notes + _vona_notes(vona_dict)

    return Recommendation(
        primary=primary,
        rule=rule,
        top=top,
        by_position=by_position,
        vona=vona_dict,
        notes=tuple(notes),
    )
