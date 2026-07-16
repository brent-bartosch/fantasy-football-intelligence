#!/usr/bin/env python3
"""Draft-day assistant — the plain-terminal shell (Phase 4 / Task 13).

A dumb renderer over `ffi.draft.session.DraftSession`: all behavior lives in
the headless core (testable/drillable without a terminal). This shell wires up
preflight (board vintage, paper floor, and — for live mode — the Yahoo poller),
then runs a `select.select` loop that calls `session.tick()` between operator
inputs. No threads, no curses (YAGNI): a draft is slow enough for a 0.5s poll
tick, and a plain terminal is what the rehearsal ladder (Task 17) drills.

Retires the dead v1 (psycopg2/14-team/adjusted_rankings assistant); git history
preserves it.

Commands:
  <enter> / r     refresh + recommendation for our (next) pick
  p <name>        manual pick for the seat on the clock (fuzzy match)
  u               undo the last (manual) pick
  b [pos]         best available (overall, or one position)
  s               status (mode, clock, our roster, vintage)
  m live|manual|paper   operator mode set
  q               quit
"""
import argparse
import datetime
import select
import sys
from pathlib import Path

from ffi.db import connect
from ffi.draft.modes import Mode, ModeMachine
from ffi.draft.poller import DraftPoller, build_resolver
from ffi.draft.session import (
    AmbiguousPickError,
    DraftSession,
    SessionConfig,
    write_paper_board,
)
from ffi.draft.state import DraftLog
from ffi.ids import team_slot
from ffi.scoring.config import load_config_v1
from ffi.signals_apply import adjusted_pool, cumulative_pct
from ffi.sim.strategy import DEPLOYED_PARAMS
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.yahoo_client import (
    ensure_fresh_token,
    get_league,
    get_session,
    yahoo_call,
)

STALE_HOURS = 36  # ADR D2 — same threshold as run_sim_farm.build_data_vintage


def _load_board(conn, scenario: str):
    """SEAM (Task 15): the single board-load point. Uses signal-based
    `adjusted_pool` when any confirmed adjustment exists (a human keystroke
    moved a board number, design §4.7 -- see `ffi.signals_apply`), else the
    plain, reproducible `build_pool`. Keep all board loading behind this one
    function."""
    cum = cumulative_pct(conn)
    if not cum:
        return build_pool(conn, scenario)
    max_pct = max(abs(v) for v in cum.values())
    print(f"board includes {len(cum)} signal adjustments, cum-cap max {max_pct:.0%}")
    return adjusted_pool(conn, scenario)


def check_board_vintage(conn, scenario: str, override_stale: bool) -> dict:
    """ADR D2 staleness gate, mirroring `run_sim_farm.build_data_vintage`'s
    query pattern: refuse (SystemExit) on a missing snapshot, a missing
    valuation, or an ADP/valuation snapshot MISMATCH (always — that is silent
    semantic drift, never overridable). A >36h-stale ADP snapshot refuses too,
    but `--override-stale` downgrades that one case to a loud warning and marks
    the returned vintage `degraded=True`."""
    config_version = load_config_v1().version
    with conn.cursor() as cur:
        cur.execute(
            "SELECT snapshot_id, fetched_at FROM raw.sleeper_projections "
            "WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            raise SystemExit(
                "draft_assistant: no season-level Sleeper snapshot at all — run "
                "`uv run python scripts/ingest_sleeper.py --season 2026` first"
            )
        adp_snapshot_id, adp_fetched_at = row
        cur.execute(
            "SELECT max((params->>'snapshot_id')::int), max(computed_at) "
            "FROM valuation.player_value WHERE config_version=%s AND scenario=%s",
            (config_version, scenario),
        )
        valuation_snapshot_id, valuation_computed_at = cur.fetchone()

    if valuation_snapshot_id is None:
        raise SystemExit(
            f"draft_assistant: no valuation.player_value rows for scenario={scenario!r} "
            "— run `uv run python scripts/build_valuation.py` first"
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    age_hours = (now - adp_fetched_at).total_seconds() / 3600
    degraded = False
    if age_hours > STALE_HOURS:
        if not override_stale:
            raise SystemExit(
                f"draft_assistant: season Sleeper snapshot {age_hours:.0f}h old "
                f"(> {STALE_HOURS}h) — refusing to draft from stale ADP. Re-run "
                "`uv run python scripts/ingest_sleeper.py --season 2026`, or pass "
                "--override-stale to draft from it anyway (ADR D2)."
            )
        degraded = True
        print(
            f"WARNING: ADP snapshot is {age_hours:.0f}h old (> {STALE_HOURS}h); "
            "--override-stale given — drafting from stale ADP (ADR D2)."
        )

    if valuation_snapshot_id != adp_snapshot_id:
        raise SystemExit(
            f"draft_assistant: valuation/ADP snapshot mismatch for scenario={scenario!r} "
            f"— valuation built from {valuation_snapshot_id} but latest ADP is "
            f"{adp_snapshot_id}. VORP/tier and ADP would be drawn from two different "
            "Sleeper pulls. Rebuild: `uv run python scripts/build_valuation.py`."
        )

    return {
        "adp_snapshot_id": adp_snapshot_id,
        "adp_age_hours": round(age_hours, 2),
        "valuation_snapshot_id": valuation_snapshot_id,
        "valuation_computed_at": valuation_computed_at.isoformat(),
        "degraded": degraded,
    }


def _live_team_slots(lg) -> dict:
    """team_key -> franchise slot from Yahoo's live `lg.teams()` (the current
    season's `teams` rows don't exist yet, so we can't use
    `poller.load_team_slots`). Slot convention is `ffi.ids.team_slot`."""
    teams = yahoo_call(lg.teams)
    slots = {tk: team_slot(tk) for tk in teams}
    if len(slots) != 12:
        raise SystemExit(
            f"draft_assistant: expected 12 teams from Yahoo, got {len(slots)}: "
            f"{sorted(slots)}"
        )
    return slots


def _build_live_poller(cfg: SessionConfig, conn, log: DraftLog) -> DraftPoller:
    """Live Yahoo poller. NOTE: the live poll path (field-mapping of
    `lg.draft_results` into the poller's pick dicts, real 999/auth behavior) is
    exercised in Task 17's rehearsal drills — never here (this task makes zero
    live Yahoo calls; the manual verification runs under --no-poll)."""
    sc = get_session()
    ensure_fresh_token(sc, margin_s=900)
    lg = get_league(sc, cfg.league_key)
    yahoo_call(lg.draft_results)  # one probe: confirm the endpoint answers
    team_slots = _live_team_slots(lg)
    resolve = build_resolver(conn)

    def fetch():
        ensure_fresh_token(sc, margin_s=900)  # ADR D4: proactive, every fetch
        return yahoo_call(lg.draft_results)

    return DraftPoller(fetch, resolve, team_slots, log)


def _print(lines) -> None:
    for ln in lines:
        print(ln)


def _dispatch(session: DraftSession, cmd: str) -> bool:
    """Handle one operator command. Returns False to quit. Operator-facing
    input errors (bad name, ambiguity, nothing to undo) are printed and the
    loop continues; anything else propagates and crashes (fail-loud)."""
    if cmd in ("", "r"):
        _print(session.recommendation_lines())
        return True
    if cmd == "q":
        return False
    if cmd == "u":
        try:
            session.undo_last()
            print("undone.")
            _print(session.status_lines())
        except ValueError as e:
            print(f"! {e}")
        return True
    if cmd == "s":
        _print(session.status_lines())
        return True
    if cmd == "b" or cmd.startswith("b "):
        pos = cmd[2:].strip() or None
        _print(session.board_lines(pos))
        return True
    if cmd.startswith("m "):
        try:
            _print(session.set_mode(cmd[2:].strip()))
        except ValueError as e:
            print(f"! {e}")
        return True
    if cmd.startswith("p "):
        try:
            picked = session.manual_pick(cmd[2:].strip())
            print(f"drafted: {picked.name} ({picked.pos}) at overall {picked.overall}")
            _print(session.recommendation_lines())
        except (AmbiguousPickError, ValueError) as e:
            print(f"! {e}")
        return True
    print(f"! unknown command {cmd!r} — try: <enter> p u b s m q")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Draft-day assistant")
    ap.add_argument("--league-key", default="461.l.326814")
    ap.add_argument(
        "--our-slot", type=int, required=True, help="our franchise slot (1-12)"
    )
    ap.add_argument(
        "--position", type=int, required=True, help="our draft position (1-12)"
    )
    ap.add_argument("--scenario", default="qb_hoard_12")
    ap.add_argument("--resume", action="store_true", help="replay an existing log")
    ap.add_argument("--no-poll", action="store_true", help="pure MANUAL, no Yahoo")
    ap.add_argument(
        "--override-stale", action="store_true", help="draft from >36h ADP (ADR D2)"
    )
    ap.add_argument("--log-path", type=Path, default=None)
    args = ap.parse_args()

    conn = connect()

    # --- Preflight (ADR D4) ------------------------------------------------
    vintage = check_board_vintage(conn, args.scenario, args.override_stale)
    pool = _load_board(conn, args.scenario)
    priors = build_slot_priors(conn)

    cfg = SessionConfig(
        league_key=args.league_key,
        our_franchise_slot=args.our_slot,
        our_position=args.position,
        scenario=args.scenario,
        log_path=args.log_path,
        board_vintage=vintage,
        # Roster-construction strategy: the SINGLE SOURCE OF TRUTH lives in
        # ffi.sim.strategy.DEPLOYED_PARAMS (QB3 late + TE cap 2; see its docstring
        # for the +19pp playoff% rationale). Importing it here -- rather than
        # re-spelling the knobs -- is what keeps the demo/nightly views from
        # drifting from what the assistant actually ships.
        params=DEPLOYED_PARAMS,
    )

    # The PAPER floor is always written before the room opens (mode-independent).
    paper_path = Path("reports") / f"paper-board-{datetime.date.today().isoformat()}.md"
    write_paper_board(pool, paper_path)
    print(f"paper board written: {paper_path}")

    machine = ModeMachine(mode=Mode.MANUAL) if args.no_poll else ModeMachine()

    # The session owns the ONE DraftLog handle for this process. Build it first,
    # then wire the poller to `session.log` -- never a second DraftLog on the
    # same file (two _next_seq counters corrupt the log; ship-blocker fix).
    try:
        if args.resume:
            session = DraftSession.resume(cfg, pool, priors, machine)
        else:
            session = DraftSession(
                cfg, pool, priors, None, machine, DraftLog(cfg.log_path)
            )
    except ValueError as e:
        # Startup preconditions (non-empty log without --resume, malformed log)
        # are operator-fixable — exit clean with the message, not a stack trace.
        raise SystemExit(f"draft_assistant: {e}")

    if not args.no_poll:
        session.attach_poller(_build_live_poller(cfg, conn, session.log))

    print(f"log: {cfg.log_path}")
    _print(session.status_lines())
    _print(session.recommendation_lines())
    print(
        "\ncommands: <enter>/r recommend · p <name> · u undo · b [pos] · s status · "
        "m live|manual|paper · q quit\n"
    )

    # --- Loop --------------------------------------------------------------
    try:
        running = True
        while running:
            _print(session.tick())
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if not ready:
                continue
            line = sys.stdin.readline()
            if not line:  # EOF (piped input exhausted)
                break
            running = _dispatch(session, line.strip())
    except KeyboardInterrupt:
        print("\nbye — log is durable; --resume to continue.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
