#!/usr/bin/env python3
"""Human confirm gate for signals.signals (Task 15, design §4.7): nothing
moves a board number without a keystroke here. Interactive triage of pending
signals -> `confirmed` (optionally with a capped adjustment applied via
`ffi.signals_apply.apply_adjustment`) or `denied`.

Unmatched signals (xwalk_id NULL, no resolved player) can only be confirmed
informationally (pct=0) or denied -- an adjustment REQUIRES a resolved
player, and `confirm_signal` refuses any nonzero pct for one, loud, before
any write.
"""
import argparse

from ffi.db import connect
from ffi.signals_apply import PER_SIGNAL_CAP, AdjustmentCapError, apply_adjustment


def list_pending(conn) -> list[dict]:
    """Pending signals, oldest first: id, title, impact, player (name or
    'UNMATCHED'), url."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT signal_id, title, impact, player_name, xwalk_id, evidence_url "
            "FROM signals.signals WHERE status = 'pending' ORDER BY fetched_at"
        )
        rows = cur.fetchall()
    return [
        {
            "signal_id": sid,
            "title": title,
            "impact": impact,
            "player": name or "UNMATCHED",
            "xwalk_id": xwalk_id,
            "url": url,
        }
        for sid, title, impact, name, xwalk_id, url in rows
    ]


def deny_signal(conn, signal_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signals.signals SET status='denied', decided_at=now() "
            "WHERE signal_id=%s AND status='pending'",
            (signal_id,),
        )
    conn.commit()


def confirm_signal(conn, signal_id: int, pct: float, note: str = "") -> int | None:
    """Confirm a pending signal. `pct=0` is confirm-informational (status
    flips to `confirmed`, no adjustment row). A nonzero `pct` requires a
    resolved player -- refuses (AdjustmentCapError, no write at all) before
    touching status if the signal's xwalk_id is NULL. Returns the new
    adjustment_id, or None for an informational confirm."""
    if abs(pct) > PER_SIGNAL_CAP + 1e-9:
        raise AdjustmentCapError(
            f"pct={pct:+.4f} exceeds the per-signal cap of ±{PER_SIGNAL_CAP:.0%}"
        )
    if pct != 0:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT xwalk_id FROM signals.signals WHERE signal_id=%s", (signal_id,)
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            raise AdjustmentCapError(
                f"signal_id={signal_id} has no resolved player (xwalk_id NULL) -- "
                "only an informational confirm (pct=0) or deny is possible"
            )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signals.signals SET status='confirmed', decided_at=now() "
            "WHERE signal_id=%s AND status='pending'",
            (signal_id,),
        )
        if cur.rowcount == 0:
            # Matches zero rows for a nonexistent signal_id AND for one that
            # already isn't 'pending' (confirmed/denied) -- either way there
            # is nothing to confirm. Without this check the UPDATE silently
            # no-ops and the pct=0 path returns None indistinguishable from a
            # real confirm (a fat-fingered id would look like it worked).
            conn.rollback()
            raise ValueError(
                f"signal_id={signal_id} does not exist or is not pending -- "
                "nothing to confirm"
            )

    adjustment_id = None
    if pct != 0:
        try:
            adjustment_id = apply_adjustment(conn, signal_id, pct, note)
        except AdjustmentCapError:
            # FAIL-LOUD Level 3: no partial writes -- the status flip above
            # is part of the same uncommitted transaction, so rolling back
            # here undoes it too. The signal stays 'pending'; re-raise so the
            # CLI/caller sees exactly why nothing happened.
            conn.rollback()
            raise
    conn.commit()
    return adjustment_id


def _format_signal(s: dict) -> str:
    return (
        f"[{s['signal_id']}] {s['title']}\n"
        f"    player: {s['player']}   impact: {s['impact'] or '-'}\n"
        f"    {s['url']}"
    )


def run_interactive(conn) -> None:
    pending = list_pending(conn)
    if not pending:
        print("no pending signals.")
        return
    for s in pending:
        print(_format_signal(s))
        action = input("  [c]onfirm [d]eny [s]kip [q]uit > ").strip().lower()
        if action == "q":
            break
        if action in ("s", ""):
            continue
        if action == "d":
            deny_signal(conn, s["signal_id"])
            print("  denied.")
            continue
        if action == "c":
            prompt = "  pct (signed, e.g. 0.05 or -0.03; 0 = informational) > "
            if s["xwalk_id"] is None:
                prompt = (
                    "  UNMATCHED player -- only pct=0 (informational) is allowed > "
                )
            raw = input(prompt).strip()
            try:
                pct = float(raw)
            except ValueError:
                print(f"  ! not a number: {raw!r} -- skipping this signal.")
                continue
            try:
                adjustment_id = confirm_signal(conn, s["signal_id"], pct)
            except AdjustmentCapError as e:
                print(f"  ! refused: {e}")
                continue
            if adjustment_id is None:
                print("  confirmed (informational, no adjustment).")
            else:
                print(f"  confirmed; adjustment_id={adjustment_id}, pct={pct:+.2%}")
            continue
        print(f"  ! unknown action {action!r} -- skipping this signal.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--list",
        action="store_true",
        help="print pending signals and exit (non-interactive)",
    )
    args = ap.parse_args()
    conn = connect()
    if args.list:
        pending = list_pending(conn)
        if not pending:
            print("no pending signals.")
        for s in pending:
            print(_format_signal(s))
        return
    run_interactive(conn)


if __name__ == "__main__":
    main()
