"""Shared synthetic pool/priors fixture for the calibration harness (Phase 4
Task 2) and its consumers (Tasks 3, 8). Plain module, not a conftest fixture,
so any test file can `from simfixtures import synthetic_pool, synthetic_priors`.

Sized per the 2026-07-10 plan amendment: the brief's original "60-player,
5 positions x 12" fixture cannot complete a full `run_draft` (12 teams x 19
rounds = 228 picks) -- the engine's starter floors alone
(QB2/RB2/WR3/TE1/K1/DEF1, see `ffi.sim.opponent.STARTERS`) need >= 120
players leaguewide, and every position runs dry long before round 19 with
only 60 total (verified: `make_strategy_fn` raises at round 5). This module
keeps the brief's *shape* -- QBs carry the pool's top proj_points/vorp,
ADP is assigned ascending 1..N in position-block order, `synthetic_priors`
puts a near-certain QB share in round 1 and a flat share elsewhere -- at a
scale (~342 players) that lets a real draft run to completion, matching
`tests/test_draft_engine.py`'s existing toy-pool convention.
"""
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors

# (position, count, top proj_points, per-rank decrement). QB's floor
# (400 - 69*4 = 124) sits above every other position's ceiling, so QB is
# strictly the highest proj_points/vorp position in the pool. Counts are
# sized generously above the brief's ~350 suggestion: `synthetic_priors`'s
# flat 1/6 share for rounds 2-19 pushes leaguewide demand on EVERY position
# (not just QB) to roughly (18 rounds x 12 seats) / 6 =~ 36 picks, and QB
# carries an additional round-1 surge on top of that -- verified empirically
# that smaller counts (e.g. QB=30) run the pool dry mid-draft and raise
# `ValueError` from `ffi.sim.opponent.opponent_pick`.
_SPECS = [
    ("QB", 70, 400.0, 4.0),
    ("RB", 90, 150.0, 1.0),
    ("WR", 120, 140.0, 0.8),
    ("TE", 50, 100.0, 1.2),
    ("K", 40, 90.0, 1.0),
    ("DEF", 40, 90.0, 1.0),
]
_REPLACEMENT_LEVEL = 50.0  # flat baseline for vorp = proj_points - replacement


def synthetic_pool() -> list[PoolPlayer]:
    """~342 PoolPlayers across all 6 positions, enough depth per position for
    a full 12-team/19-round draft to complete under the engine's starter
    floors. ADP is assigned ascending 1..N across the whole pool in
    position-block order (QB block first); QBs hold the top proj_points/vorp
    in the entire pool."""
    players = []
    adp = 1
    for pos, n, top_proj, decrement in _SPECS:
        for i in range(n):
            proj = top_proj - i * decrement
            players.append(
                PoolPlayer(
                    ref=f"{pos}{i}",
                    name=f"{pos}{i}",
                    position=pos,
                    proj_points=float(proj),
                    vorp=float(proj - _REPLACEMENT_LEVEL),
                    tier=1 + i // 5,
                    adp=float(adp),
                    gsis_id=None,
                )
            )
            adp += 1
    return players


_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def synthetic_priors(qb_share_r1: float = 0.97) -> SlotPriors:
    """Hand-built `SlotPriors` covering every (slot, round) in 1-12 x 1-19:
    round 1 puts `qb_share_r1` on QB with the remainder split evenly across
    the other 5 positions; every other round is a flat 1/6 share."""
    other_share_r1 = (1.0 - qb_share_r1) / (len(_POSITIONS) - 1)
    flat_share = 1.0 / len(_POSITIONS)

    pos_share = {}
    for slot in range(1, 13):
        for rnd in range(1, 20):
            if rnd == 1:
                pos_share[(slot, rnd)] = {
                    pos: (qb_share_r1 if pos == "QB" else other_share_r1)
                    for pos in _POSITIONS
                }
            else:
                pos_share[(slot, rnd)] = {pos: flat_share for pos in _POSITIONS}
    return SlotPriors(latest_season=2025, pos_share=pos_share, params={})
