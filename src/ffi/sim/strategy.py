"""Our-seat strategy logic (Phase 3 / Task 8): the knobs the sim farm grids.

`StrategyParams` is a frozen, hashable dataclass of strategy knobs; Task 12's
sim farm builds a grid of these and, for each, calls `make_strategy_fn` to get
a `PickFn` (the same seam Task 7's `run_draft` expects for `our_pick_fn`).
Task 11's backtests use fixed "REF" `StrategyParams` instances the same way.

Decision order inside the returned fn (checked in this order, every call):

1. **Feasibility force.** If `required_picks(counts) == picks_left_after`
   (see `ffi.sim.opponent`), the roster has no picks to spare on anything
   that doesn't move a starter/FLEX slot closer to filled. Candidates are
   restricted to "unmet" positions -- those where drafting one more would
   actually reduce `required_picks` (a starter slot still open, or a
   FLEX-eligible position (RB/WR/TE) when FLEX isn't yet covered) -- scored
   the same way as rule 4.

2. **QB deadline force.** Let `n` be the smallest integer > `counts["QB"]`
   (up to `len(qb_by_round)`) such that `round >= qb_by_round[n - 1]` -- i.e.
   the earliest still-unmet QB-count deadline that has arrived. If such an
   `n` exists and the force is actually available and feasible (and, as a
   safety net beyond the brief, still under the QB cap), take the best
   available QB by plain vorp. Only the single smallest `n` is ever
   consulted -- if its force can't be taken, control falls through to the
   next rule rather than trying a later (larger) deadline.

3. **DEF/K force.** `round >= defk_round` and no DEF yet -> best DEF (by
   vorp). Independently, `round >= defk_round + 1` and no K yet -> best K.
   DEF is checked first, so if both are simultaneously overdue (e.g. an
   earlier DEF force fell through infeasible), DEF wins this call and K's
   turn comes on a later call.

4. **Otherwise.** Over every position that is simultaneously: available,
   feasible (`ffi.sim.opponent.feasible`), under its cap (`caps`), not QB
   once `counts["QB"] >= len(qb_by_round)` (the plan is done -- don't
   voluntarily hoard more), not QB while `round < qb_not_before[counts["QB"]]`
   (the delay knob below), and not DEF/K before `defk_round` (no early
   luxury DEF/K picks) -- take the top `CAND_WINDOW` (`ffi.sim.opponent`)
   candidates, score each `vorp + tier_break_bonus * is_last_in_tier`, and
   argmax. For QB specifically, `qb_tier_targets` (below) may additionally
   narrow the candidate pool by tier before that top-`CAND_WINDOW` slice is
   taken. `is_last_in_tier` is computed against the position's full
   *available* list (not just the candidate window, and not the
   `qb_tier_targets`-narrowed one): true when no other available player at
   that position shares the candidate's tier, so taking this one closes out
   the tier. Ties (rare, but real with `tier_break_bonus`) break by lower ADP
   (a real ADP always beats `None`), then by name -- both are pure functions
   of the candidates and stable across runs, giving the same result for the
   same board every time.

A note on forces vs. legality: rules 2 and 3 describe hard-sounding
"forces," but a strategy bug that returns an infeasible pick makes
`run_draft` raise (Task 7 re-validates `our_pick_fn`'s output). So both
forces always re-check `feasible(...)` before committing, and silently
fall through to the next rule when the force isn't (yet) legal or
available -- e.g. deep in the draft, forcing a QB past the plan when QB is
already at its 2-starter requirement doesn't free up any required pick, so
if there's no slack left it would be infeasible, and the fn defers to
whatever rule 3/4 can legally offer instead.

`qb_not_before` (Task 11 plan amendment): a rule-4-only delay knob, added
because Task 11's backtests proved `qb_by_round` deadlines never bind under
`qb_hoard_12` (the top ~25 VORP are all QBs, so rule 4's argmax drafts a QB
immediately regardless of the deadline round -- every REF strategy produced
an identical draft). `qb_not_before[n]` (n = `counts["QB"]` at the time of
the call, 0-indexed) is the earliest round rule 4 may voluntarily take QB
#(n+1) -- index 0 gates the 1st QB, index 1 the 2nd, and so on. It ONLY
narrows rule 4's candidate set; rules 1 (feasibility force) and 2 (QB
deadline force) are untouched, so a deadline can still force a QB earlier
than its not-before round -- e.g. `qb_by_round=(3,...)` with
`qb_not_before=(5,...)` still forces QB at round 3 via rule 2. A
misconfigured pair where `qb_by_round[n] < qb_not_before[n]` therefore lets
the deadline win rather than deadlocking the draft -- an intended fail-safe,
not a bug. `make_strategy_fn` raises `ValueError` if `qb_not_before` is
shorter than `qb_by_round` (a grid config that couldn't possibly gate every
planned QB is rejected loudly rather than silently under-indexing).

`qb_tier_targets` (Phase 4 Task 6): a rule-4-only *which*-QB filter, distinct
from `qb_not_before`'s *when*. `qb_tier_targets[n]` (n = `counts["QB"]` at the
time of the call, 0-indexed same as `qb_not_before`) caps rule 4's QB
candidates to `tier <= qb_tier_targets[n]` when voluntarily drafting QB
#(n+1); an index past the tuple's end is unrestricted (same convention as
`qb_not_before`'s length handling), and the default `()` never restricts
anything. Like `qb_not_before`, it ONLY narrows rule 4's candidate set --
rules 1-3 are untouched, so a `qb_by_round` deadline still force-takes the
best available QB by plain vorp regardless of tier if a tier target would
otherwise have excluded it (a misconfigured target, like a misconfigured
`qb_not_before`, degrades to "the deadline wins" rather than deadlocking the
draft).

Deterministic: no `rng` anywhere in this module. Every branch is a pure
function of its arguments.
"""
from dataclasses import dataclass

from ffi.sim.draft import PickFn
from ffi.sim.opponent import CAND_WINDOW, STARTERS, feasible, required_picks
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import POSITIONS


@dataclass(frozen=True)
class StrategyParams:
    scenario: str = "qb_hoard_12"  # which valuation scenario builds the pool
    qb_by_round: tuple = (2, 5, 9)  # QB #n on roster by END of round qb_by_round[n-1]
    defk_round: int = 14  # DEF forced at this round if unheld; K at defk_round+1
    caps: tuple = (("QB", 4), ("RB", 9), ("WR", 9), ("TE", 3), ("K", 2), ("DEF", 2))
    tier_break_bonus: float = 0.0  # score bump for closing out a tier
    qb_not_before: tuple = (1, 1, 1)  # QB #n not draftable (rule 4) before this round
    qb_tier_targets: tuple = ()  # QB #n (rule 4 only) capped at tier <= this[n]


def _unmet_positions(counts: dict) -> list[str]:
    """Positions where drafting one more would reduce `required_picks(counts)`
    -- an open starter slot, or a FLEX-eligible position while FLEX is open."""
    base = required_picks(counts)
    unmet = []
    for pos in STARTERS:
        c2 = dict(counts)
        c2[pos] = c2.get(pos, 0) + 1
        if required_picks(c2) < base:
            unmet.append(pos)
    return unmet


def _is_last_in_tier(player: PoolPlayer, avail_for_pos: list) -> bool:
    return not any(
        other.tier == player.tier for other in avail_for_pos if other.ref != player.ref
    )


def _score(player: PoolPlayer, avail_for_pos: list, tier_break_bonus: float) -> float:
    bonus = tier_break_bonus if _is_last_in_tier(player, avail_for_pos) else 0.0
    return player.vorp + bonus


def _adp_sort_key(player: PoolPlayer) -> tuple:
    return (player.adp is None, player.adp if player.adp is not None else 0.0)


def _pick_best(scored: list) -> PoolPlayer:
    """`scored`: list of (score, PoolPlayer). Argmax on score; ties break by
    lower ADP (None last), then by name -- both deterministic and total."""

    def key(item):
        score, player = item
        return (-score, _adp_sort_key(player), player.name)

    return min(scored, key=key)[1]


def make_strategy_fn(params: StrategyParams) -> PickFn:
    """Build a `PickFn` (see `ffi.sim.draft.PickFn`) implementing the
    decision order documented in this module's docstring for the given
    `params`."""
    if len(params.qb_not_before) < len(params.qb_by_round):
        raise ValueError(
            f"make_strategy_fn: qb_not_before (len {len(params.qb_not_before)}) is "
            f"shorter than qb_by_round (len {len(params.qb_by_round)}) -- can't gate "
            "every planned QB"
        )
    caps = dict(params.caps)

    def strategy_fn(
        avail_by_pos: dict, round_: int, counts: dict, picks_left_after: int
    ) -> PoolPlayer:
        # 1. Feasibility force.
        if required_picks(counts) == picks_left_after:
            scored = []
            for pos in _unmet_positions(counts):
                cands = avail_by_pos.get(pos) or []
                if not cands:
                    continue
                for c in cands[:CAND_WINDOW]:
                    scored.append((_score(c, cands, params.tier_break_bonus), c))
            if scored:
                return _pick_best(scored)
            # No available candidate at any unmet position -- an unexpected
            # state in a well-formed draft, but falling through to try the
            # remaining rules is strictly safer than raising here (rule 4's
            # own feasibility checks still guard against an illegal return).

        # 2. QB deadline force -- only the smallest still-unmet n.
        qb_n = counts.get("QB", 0)
        for n in range(qb_n + 1, len(params.qb_by_round) + 1):
            if round_ >= params.qb_by_round[n - 1]:
                cands = avail_by_pos.get("QB") or []
                if (
                    cands
                    and qb_n < caps.get("QB", float("inf"))
                    and feasible(counts, "QB", picks_left_after)
                ):
                    return _pick_best([(c.vorp, c) for c in cands[:CAND_WINDOW]])
                break  # don't try a later (larger) n's deadline this call

        # 3. DEF/K force.
        if (
            round_ >= params.defk_round
            and counts.get("DEF", 0) == 0
            and counts.get("DEF", 0) < caps.get("DEF", float("inf"))
        ):
            cands = avail_by_pos.get("DEF") or []
            if cands and feasible(counts, "DEF", picks_left_after):
                return _pick_best([(c.vorp, c) for c in cands[:CAND_WINDOW]])
        if (
            round_ >= params.defk_round + 1
            and counts.get("K", 0) == 0
            and counts.get("K", 0) < caps.get("K", float("inf"))
        ):
            cands = avail_by_pos.get("K") or []
            if cands and feasible(counts, "K", picks_left_after):
                return _pick_best([(c.vorp, c) for c in cands[:CAND_WINDOW]])

        # 4. Otherwise: feasible, under-cap, in-window candidates, argmax score.
        scored = []
        for pos in POSITIONS:
            if pos == "QB" and counts.get("QB", 0) >= len(params.qb_by_round):
                continue
            if (
                pos == "QB"
                and qb_n < len(params.qb_not_before)
                and round_ < params.qb_not_before[qb_n]
            ):
                continue
            if pos in ("DEF", "K") and round_ < params.defk_round:
                continue
            if counts.get(pos, 0) >= caps.get(pos, float("inf")):
                continue
            if not feasible(counts, pos, picks_left_after):
                continue
            cands = avail_by_pos.get(pos) or []
            if not cands:
                continue
            # `_is_last_in_tier` (via `_score`) is computed against `cands`,
            # so for QB it must still see the position's FULL available list
            # -- the tier filter below narrows *candidacy*, not tier-closure
            # math -- hence it's applied to a separate `filtered` view.
            filtered = cands
            if pos == "QB" and qb_n < len(params.qb_tier_targets):
                max_tier = params.qb_tier_targets[qb_n]
                filtered = [c for c in cands if c.tier <= max_tier]
                if not filtered:
                    continue
            for c in filtered[:CAND_WINDOW]:
                scored.append((_score(c, cands, params.tier_break_bonus), c))

        if not scored:
            raise ValueError(
                f"make_strategy_fn: no feasible/under-cap/in-window candidate "
                f"at round {round_} (counts={counts}, "
                f"picks_left_after={picks_left_after})"
            )
        return _pick_best(scored)

    return strategy_fn
