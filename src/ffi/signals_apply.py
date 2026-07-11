"""Capped, human-confirmed board adjustments (Phase 4 / Task 15).

`apply_adjustment` is the ONLY writer of `signals.adjustments`. It enforces
three caps, all fail-loud (`AdjustmentCapError`, no partial writes):
  - per-signal: |pct| <= 0.10
  - cumulative: |sum(pct) over a player's adjustments| <= 0.20
  - per-day: at most one adjustment per (xwalk_id, local day)
A signal must already be `status='confirmed'` with a resolved player
(`xwalk_id` NOT NULL) before an adjustment can be applied to it -- that
transition is the human's, made in `scripts/confirm_signals.py` (design
Section 4.7: nothing moves a board number without a keystroke there).

`adjusted_pool` is LIVE-BOARD-ONLY. `ffi.sim` (simulator, farm, backtest)
must never call it -- those need pool contents fully reproducible from a
snapshot for a given (config_version, scenario), and signal adjustments are
mutable, operator-driven, and dated by real wall-clock time. Only
`scripts/draft_assistant.py`'s live board loader uses it.
"""
import dataclasses
import datetime

from ffi.sim.pool import PoolPlayer, build_pool

PER_SIGNAL_CAP = 0.10
CUMULATIVE_CAP = 0.20
_EPS = 1e-9  # float-precision slack around the cap boundaries (0.10 stored as REAL)


class AdjustmentCapError(Exception):
    """A requested adjustment is not eligible: the signal doesn't exist, isn't
    `confirmed`, has no resolved player, or would violate the per-signal,
    cumulative, or per-day cap. Raised before any write to
    `signals.adjustments` -- callers never see a partial write."""


def apply_adjustment(conn, signal_id: int, pct: float, note: str = "") -> int:
    """Insert one `signals.adjustments` row for `signal_id`, or raise
    `AdjustmentCapError` and write nothing.

    All reads/locks/checks happen in the caller's current transaction (this
    function does not commit); `SELECT ... FOR UPDATE` on the signal row and
    the player's existing adjustment rows makes the caps race-free even
    though this is a single-operator tool -- the correctness is cheap here.
    """
    if abs(pct) > PER_SIGNAL_CAP + _EPS:
        raise AdjustmentCapError(
            f"pct={pct:+.4f} exceeds the per-signal cap of ±{PER_SIGNAL_CAP:.0%} "
            f"(signal_id={signal_id})"
        )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, xwalk_id FROM signals.signals WHERE signal_id = %s FOR UPDATE",
            (signal_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AdjustmentCapError(f"signal_id={signal_id} does not exist")
        status, xwalk_id = row
        if status != "confirmed":
            raise AdjustmentCapError(
                f"signal_id={signal_id} has status={status!r}, expected 'confirmed' "
                "-- confirm the signal before applying an adjustment"
            )
        if xwalk_id is None:
            raise AdjustmentCapError(
                f"signal_id={signal_id} has no resolved player (xwalk_id NULL) -- "
                "an adjustment requires a resolved player"
            )

        cur.execute(
            "SELECT pct, applied_at FROM signals.adjustments "
            "WHERE xwalk_id = %s FOR UPDATE",
            (xwalk_id,),
        )
        existing = cur.fetchall()

        today = datetime.date.today()
        if any(applied_at.astimezone().date() == today for _, applied_at in existing):
            raise AdjustmentCapError(
                f"xwalk_id={xwalk_id} already has an adjustment applied today -- "
                "per-day cap: at most one adjustment per player per day"
            )

        existing_sum = sum(p for p, _ in existing)
        cumulative = existing_sum + pct
        if abs(cumulative) > CUMULATIVE_CAP + _EPS:
            raise AdjustmentCapError(
                f"xwalk_id={xwalk_id} cumulative pct {cumulative:+.4f} would exceed the "
                f"±{CUMULATIVE_CAP:.0%} cumulative cap (existing sum {existing_sum:+.4f} "
                f"+ this {pct:+.4f})"
            )

        cur.execute(
            "INSERT INTO signals.adjustments (signal_id, xwalk_id, pct, note) "
            "VALUES (%s, %s, %s, %s) RETURNING adjustment_id",
            (signal_id, xwalk_id, pct, note),
        )
        return cur.fetchone()[0]


def cumulative_pct(conn) -> dict[int, float]:
    """xwalk_id -> clamped-sum of applied pct across `signals.adjustments`.
    The clamp to [-0.20, 0.20] is defense-in-depth only: `apply_adjustment`'s
    cumulative cap already guarantees the raw sum never exceeds it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT xwalk_id, sum(pct) FROM signals.adjustments GROUP BY xwalk_id"
        )
        rows = cur.fetchall()
    return {
        xwalk_id: max(-CUMULATIVE_CAP, min(CUMULATIVE_CAP, float(s)))
        for xwalk_id, s in rows
    }


def _reorder(players: list[PoolPlayer]) -> list[PoolPlayer]:
    """Re-apply `build_pool`'s ordering convention (real-ADP ascending, then
    None-ADP vorp-descending) after a pct shift may have moved a player's
    vorp. Duplicated here rather than imported from `ffi.sim.pool` because
    `build_pool` doesn't expose its tail-sort as a standalone helper."""
    real_adp = sorted((p for p in players if p.adp is not None), key=lambda p: p.adp)
    none_adp = sorted(
        (p for p in players if p.adp is None), key=lambda p: p.vorp, reverse=True
    )
    return real_adp + none_adp


def adjusted_pool(conn, scenario: str) -> list[PoolPlayer]:
    """`build_pool` output with `proj_points`/`vorp` shifted for players
    carrying a confirmed adjustment: adjusted_proj = proj*(1+cum),
    adjusted_vorp = vorp + proj*cum. Baseline (replacement rank) is
    unchanged -- a <=20% nudge on a handful of players does not move which
    player is the last starter at a position, so re-deriving VORP from
    scratch is unnecessary. Pool order is re-sorted by `build_pool`'s
    convention after shifting, since a vorp shift can move a None-ADP
    player's rank within that tail.

    LIVE-BOARD-ONLY -- see module docstring. Sims/farm/backtest call
    `build_pool` directly, never this.
    """
    pool = build_pool(conn, scenario)
    cum = cumulative_pct(conn)
    if not cum:
        return pool

    with conn.cursor() as cur:
        cur.execute(
            "SELECT xwalk_id, sleeper_id FROM public.player_id_xwalk WHERE xwalk_id = ANY(%s)",
            (list(cum.keys()),),
        )
        ref_by_xwalk = dict(cur.fetchall())
    pct_by_ref = {
        ref_by_xwalk[xwalk_id]: pct
        for xwalk_id, pct in cum.items()
        if xwalk_id in ref_by_xwalk and ref_by_xwalk[xwalk_id] is not None
    }
    if not pct_by_ref:
        return pool

    shifted = [
        p if p.ref not in pct_by_ref else _shift(p, pct_by_ref[p.ref]) for p in pool
    ]
    return _reorder(shifted)


def _shift(p: PoolPlayer, pct: float) -> PoolPlayer:
    return dataclasses.replace(
        p,
        proj_points=p.proj_points * (1 + pct),
        vorp=p.vorp + p.proj_points * pct,
    )
