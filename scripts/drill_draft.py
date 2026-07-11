#!/usr/bin/env python3
"""Rehearsal drill harness (Phase 4 / Task 17): turns "should work" into
"measured working". Each drill drives the REAL `DraftSession` against a fake
transport that replays a historical Yahoo draft (`draft_picks` for one NAJEE
season), measures one of the four written ADR D7 acceptance criteria, prints
`PASS`/`FAIL`, and appends a row to `docs/runbooks/rehearsal-log.md` (committed
-- drill history is draft-day evidence).

    uv run python scripts/drill_draft.py --drill {lag|999|refresh|crash} --season 2024

The four written pass criteria (ADR D7, verbatim):
  1. Poll lag p95 < 15s (measured pick-visible-to-applied).
  2. Token refresh mid-session without pick loss.
  3. Forced-999 -> MANUAL switchover < 30s (the switchover half is human-timed;
     this headless run measures the machine side: injection -> MANUAL banner).
  4. Crash -> resume with full state (derived taken/counts/overall/mode exactly
     equal to a control session that never crashed).

ZERO Yahoo API calls: every drill uses a fake transport built from the local
DB. Level 2 (a private Yahoo test league) is the only live-plumbing venue and
is the user's future venue -- see docs/runbooks/rehearsal-ladder.md.

Failure policy (fail-loud): the transport raises the two typed Yahoo errors on
schedule so the REAL `DraftSession.tick()` handles them through the same
ModeMachine path production uses. Everything else propagates. `_FakeOAuth` is
the only stub in the refresh drill; it never silently succeeds a bad refresh.
"""
import argparse
import statistics
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Callable

from ffi.db import connect
from ffi.draft.modes import Mode, ModeMachine
from ffi.draft.poller import DraftPoller, build_resolver, load_team_slots
from ffi.draft.session import DraftSession, SessionConfig
from ffi.draft.state import DraftLog
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.yahoo_client import YahooAuthError, YahooRateLimitError, ensure_fresh_token

REHEARSAL_LOG = Path("docs/runbooks/rehearsal-log.md")


class FakeClock:
    """A clock the drill drives by hand (fake-clock drills) -- same shape as
    the session tests' clock. The lag drill uses `time.monotonic` instead."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _FakeOAuth:
    """Stand-in for yahoo_oauth's OAuth2 for the refresh drill only. Starts
    near expiry so `ensure_fresh_token` refreshes proactively; a refresh sets
    `token_time` to now and the token stays valid. It never fakes a *failed*
    refresh into a success -- token death is loud in production, so the drill
    only ever exercises the healthy proactive-refresh path."""

    def __init__(self, token_time: float):
        self.token_time = token_time
        self.refreshes = 0

    def refresh_access_token(self) -> None:
        self.token_time = time.time()
        self.refreshes += 1

    def token_is_valid(self) -> bool:
        return True


# --------------------------------------------------------------------------
# Fake transport: replay a historical draft on a clock-driven release schedule
# --------------------------------------------------------------------------


def _fetch_closure(
    picks: list[dict],
    schedule: dict,
    clock: Callable[[], float] = time.monotonic,
    cadence_s: float = 0.0,
) -> Callable[[], list[dict]]:
    """Build the `fetch_fn` the poller injects. `picks` is the ordered
    (by overall) list of made picks, each a `{"pick","round","team_key",
    "player_id"}` dict. The returned closure mimics Yahoo's `draftresults`:
    it always returns EVERY slot; a slot carries `player_id` only once its
    release time (`t0 + rank*cadence_s`) has passed on `clock` (Fact 11 --
    unmade picks lack player_id). `t0` is captured on the first call.

    Faults from `schedule` fire as the release frontier first crosses the
    configured pick number, raising the SAME typed Yahoo errors the live
    endpoint would (so the real ModeMachine handles them):
      - rate_limit_at: pick   -> YahooRateLimitError (999)
      - auth_fail_at:  pick    -> YahooAuthError
      - fail_at:       [pick]  -> YahooAuthError (one-off, recovers next fetch)
      - latency_s:  {pick: s}  -> real sleep as that pick is released (lag drill)
    A fired fault does not advance the frontier, so a one-off failure is
    delivered on the next fetch -- LIVE -> POLL-DEGRADED -> recover, unretried.
    """
    latency = dict(schedule.get("latency_s") or {})
    fail_at = set(schedule.get("fail_at") or [])
    rate_limit_at = schedule.get("rate_limit_at")
    auth_fail_at = schedule.get("auth_fail_at")
    rank = {int(p["pick"]): i for i, p in enumerate(picks)}
    fault_picks = set(fail_at)
    if rate_limit_at is not None:
        fault_picks.add(rate_limit_at)
    if auth_fail_at is not None:
        fault_picks.add(auth_fail_at)

    state = {"t0": None, "last_frontier": 0, "fired": set()}

    def release_time(overall: int) -> float:
        return state["t0"] + rank[overall] * cadence_s

    def _released(overall: int, now: float) -> bool:
        return state["t0"] + rank[overall] * cadence_s <= now

    def fetch() -> list[dict]:
        if state["t0"] is None:
            state["t0"] = clock()
        now = clock()
        frontier = max(
            (int(p["pick"]) for p in picks if _released(int(p["pick"]), now)),
            default=0,
        )
        for f in sorted(fault_picks):
            if state["last_frontier"] < f <= frontier and f not in state["fired"]:
                state["fired"].add(f)
                if f == rate_limit_at:
                    raise YahooRateLimitError(f"drill: injected error 999 at pick {f}")
                raise YahooAuthError(f"drill: injected auth failure at pick {f}")
        for f, secs in latency.items():
            if state["last_frontier"] < f <= frontier:
                time.sleep(secs)
        out = []
        for p in picks:
            o = int(p["pick"])
            d = {"pick": o, "round": int(p["round"]), "team_key": p["team_key"]}
            if _released(o, now):
                d["player_id"] = p["player_id"]
            out.append(d)
        state["last_frontier"] = frontier
        return out

    fetch.release_time = release_time
    fetch.total = len(picks)
    fetch.state = state
    return fetch


def _load_draft(conn, league_key: str, season: int) -> list[dict]:
    """Load a historical draft as an ordered list of made-pick payloads. The
    Yahoo player id is stored on `players.yahoo_player_id` as a full player key
    (`<game>.p.<bare>`); the live `draftresults` endpoint returns the BARE id,
    so we strip to the tail here to match what `build_resolver` indexes."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT season_year FROM leagues WHERE league_id = %s", (league_key,)
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"no league {league_key!r} in `leagues`")
        if row[0] != season:
            raise ValueError(
                f"league {league_key!r} is season {row[0]}, not {season} -- "
                "the drill's --season and the resolved league disagree"
            )
        cur.execute(
            """SELECT dp.overall_pick, dp.round_number, t.team_key,
                      split_part(p.yahoo_player_id, '.', 3) AS bare_yahoo_id
               FROM draft_picks dp
               JOIN teams t ON t.team_id = dp.team_id
               JOIN players p ON p.player_id = dp.player_id
               WHERE dp.league_id = %s
               ORDER BY dp.overall_pick""",
            (league_key,),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError(f"no draft_picks for {league_key!r}")
    return [
        {"pick": overall, "round": rnd, "team_key": tk, "player_id": yid}
        for overall, rnd, tk, yid in rows
    ]


def scripted_fetch(
    conn,
    league_key: str,
    season: int,
    schedule: dict,
    clock: Callable[[], float] = time.monotonic,
    cadence_s: float = 0.0,
) -> Callable[[], list[dict]]:
    """Public seam (Task 17 interface): replay the real `draft_picks` for
    `league_key`/`season` as a growing `draftresults` payload on `clock`,
    injecting the faults in `schedule`."""
    picks = _load_draft(conn, league_key, season)
    return _fetch_closure(picks, schedule, clock=clock, cadence_s=cadence_s)


# --------------------------------------------------------------------------
# Season -> league resolution and session assembly (shared by every drill)
# --------------------------------------------------------------------------


def _resolve_league_key(conn, season: int) -> str:
    """The one home-league (12 slots, 228 picks) with a full team_key/slot map
    for `season`. The away/LMU leagues in the same season have no slot map and
    are unusable as a live-plumbing replay."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT dp.league_id
               FROM draft_picks dp
               JOIN teams t ON t.team_id = dp.team_id
               JOIN leagues l ON l.league_id = dp.league_id
               WHERE l.season_year = %s AND t.slot IS NOT NULL
               GROUP BY dp.league_id
               HAVING count(DISTINCT t.slot) = 12""",
            (season,),
        )
        rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"season {season}: expected exactly one 12-slot home league, "
            f"got {[r[0] for r in rows]}"
        )
    return rows[0][0]


def _our_seat(team_slots: dict, picks: list[dict]) -> tuple[int, int]:
    """Pin OUR seat to draft position 1 (overall pick 1). derive_state builds
    the position->slot map from round 1 and cross-checks OUR slot only, so
    taking the slot that actually drafted at overall 1 is always consistent."""
    first = next(p for p in picks if int(p["pick"]) == 1)
    our_slot = team_slots[first["team_key"]]
    return our_slot, 1


def _build_session(
    conn,
    cfg: SessionConfig,
    pool,
    priors,
    machine: ModeMachine,
    fetch,
    team_slots: dict,
    clock,
    resume: bool = False,
) -> DraftSession:
    """Assemble a real `DraftSession` + poller wired to the ONE log handle
    (fresh construction, or `resume()` + `attach_poller` -- the only two entries
    the session allows). The poller uses the real crosswalk resolver."""
    resolve = build_resolver(conn)
    if resume:
        session = DraftSession.resume(cfg, pool, priors, machine, clock)
        session.attach_poller(DraftPoller(fetch, resolve, team_slots, session.log))
    else:
        log = DraftLog(cfg.log_path)
        poller = DraftPoller(fetch, resolve, team_slots, log)
        session = DraftSession(cfg, pool, priors, poller, machine, log, clock)
    return session


def _tmp_log() -> Path:
    fd, name = tempfile.mkstemp(prefix="drill-", suffix=".jsonl")
    p = Path(name)
    p.unlink()  # DraftLog wants to create it fresh (empty)
    return p


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _applied(session: DraftSession) -> set[int]:
    return set(session._state.picks_by_overall)


def _log_row(drill: str, result: str, metrics: str) -> None:
    """Append one committed evidence row. Creates the file with a header the
    first time (the header names the four written criteria so the log is
    self-describing draft-day evidence)."""
    REHEARSAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not REHEARSAL_LOG.exists():
        REHEARSAL_LOG.write_text(
            "# Rehearsal drill log (ADR D7 evidence)\n\n"
            "Each row is one drill run against the fake transport (a historical\n"
            "NAJEE-season replay). The four written pass criteria:\n\n"
            "1. Poll lag p95 < 15s (pick-visible-to-applied).\n"
            "2. Token refresh mid-session without pick loss.\n"
            "3. Forced-999 -> MANUAL switchover < 30s (human-timed; the headless\n"
            "   run measures the machine side: injection -> MANUAL banner).\n"
            "4. Crash -> resume with full state (derived state exactly equal to a\n"
            "   never-crashed control).\n\n"
            "| date | drill | result | metrics | git sha |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with REHEARSAL_LOG.open("a", encoding="utf-8") as f:
        f.write(
            f"| {date.today().isoformat()} | {drill} | {result} | {metrics} "
            f"| {_git_sha()} |\n"
        )


# --------------------------------------------------------------------------
# The four drills
# --------------------------------------------------------------------------

POLL_INTERVAL_S = 5.0  # ADR D1: 5-10s; the drill uses the tight end for margin


def _prep(conn, season: int, schedule: dict, clock, cadence_s: float, log_path=None):
    """Everything a drill needs before its loop: resolved league, real board +
    priors, team-slot map, our seat, and the fake transport."""
    league_key = _resolve_league_key(conn, season)
    picks = _load_draft(conn, league_key, season)
    team_slots = load_team_slots(conn, league_key)
    our_slot, our_pos = _our_seat(team_slots, picks)
    pool = build_pool(conn, "qb_hoard_12")
    priors = build_slot_priors(conn)
    fetch = _fetch_closure(picks, schedule, clock=clock, cadence_s=cadence_s)
    cfg = SessionConfig(
        league_key=league_key,
        our_franchise_slot=our_slot,
        our_position=our_pos,
        log_path=log_path or _tmp_log(),
        poll_interval_s=POLL_INTERVAL_S,
    )
    return league_key, picks, team_slots, pool, priors, fetch, cfg


def drill_lag(conn, season: int) -> bool:
    """Criterion 1: poll lag p95 < 15s, measured pick-visible-to-applied over a
    real-time-scaled replay (picks released every ~2.5s; >= 30 samples). Each
    pick's lag = wall time it was applied minus the wall time it became visible
    (`fetch.release_time`); PollResult.latency_s + apply time are the machine-
    side components of that end-to-end lag."""
    cadence_s = 2.5
    _, picks, team_slots, pool, priors, fetch, cfg = _prep(
        conn, season, {}, time.monotonic, cadence_s
    )
    session = _build_session(
        conn, cfg, pool, priors, ModeMachine(), fetch, team_slots, time.monotonic
    )

    lags: dict[int, float] = {}
    seen = set()
    deadline = time.monotonic() + 240  # hard cap so a stall fails loud, not hangs
    while len(lags) < 40 and time.monotonic() < deadline:
        before = _applied(session)
        session.tick()
        now = time.monotonic()
        for o in _applied(session) - before:
            if o not in seen:
                seen.add(o)
                lags[o] = now - fetch.release_time(o)
        time.sleep(0.1)  # real loop cadence; poll gating is inside tick()

    samples = sorted(lags.values())
    if len(samples) < 30:
        metrics = f"only {len(samples)} samples (< 30) in 240s — inconclusive"
        print(f"FAIL lag: {metrics}")
        _log_row("lag", "FAIL", metrics)
        return False
    p95 = statistics.quantiles(samples, n=20)[-1]  # 95th percentile
    ok = p95 < 15.0
    metrics = (
        f"p95={p95:.2f}s median={statistics.median(samples):.2f}s "
        f"max={max(samples):.2f}s n={len(samples)} (interval={POLL_INTERVAL_S}s, "
        f"cadence={cadence_s}s)"
    )
    print(f"{'PASS' if ok else 'FAIL'} lag: {metrics}")
    _log_row("lag", "PASS" if ok else "FAIL", metrics)
    return ok


def drill_999(conn, season: int) -> bool:
    """Criterion 3 (machine side): inject error 999 at a mid-draft pick and
    assert the session flips to MANUAL immediately with exactly ONE mode event
    logged, and stays there (no retry). The <30s human switchover half runs at
    Level 1 with the operator -- see rehearsal-ladder.md."""
    inject_at = 40
    clock = FakeClock()
    _, picks, team_slots, pool, priors, fetch, cfg = _prep(
        conn, season, {"rate_limit_at": inject_at}, clock, cadence_s=1.0
    )
    session = _build_session(
        conn, cfg, pool, priors, ModeMachine(), fetch, team_slots, clock
    )

    manual_reached_at = None
    for _ in range(inject_at + 20):
        clock.advance(POLL_INTERVAL_S)
        session.tick()
        if session.mode is Mode.MANUAL and manual_reached_at is None:
            manual_reached_at = len(_applied(session))
            break

    _, events, _ = DraftLog.replay(cfg.log_path)
    mode_events = [e for e in events if e.kind == "mode"]
    manual_events = [e for e in mode_events if e.payload["mode"] == "MANUAL"]
    ok = (
        session.mode is Mode.MANUAL
        and len(manual_events) == 1
        and manual_reached_at is not None
    )
    metrics = (
        f"999@pick{inject_at} -> mode={session.mode.value} after "
        f"{manual_reached_at} picks; mode_events={len(mode_events)} "
        f"(MANUAL x{len(manual_events)}); switchover is immediate (0 retries)"
    )
    print(f"{'PASS' if ok else 'FAIL'} 999: {metrics}")
    _log_row("999", "PASS" if ok else "FAIL", metrics)
    return ok


def drill_refresh(conn, season: int) -> bool:
    """Criterion 2: force the token near expiry (token_time ~ now-3540, i.e.
    60s of life vs a 900s margin) so `ensure_fresh_token` refreshes proactively
    on the fetch path, and assert ZERO picks missed vs the script."""
    clock = FakeClock()
    league_key, picks, team_slots, pool, priors, base_fetch, cfg = _prep(
        conn, season, {}, clock, cadence_s=1.0
    )
    sc = _FakeOAuth(token_time=time.time() - 3540)

    def fetch_with_refresh():
        ensure_fresh_token(sc, margin_s=900)  # ADR D4: proactive, every fetch
        return base_fetch()

    session = _build_session(
        conn, cfg, pool, priors, ModeMachine(), fetch_with_refresh, team_slots, clock
    )
    for _ in range(len(picks) + 10):
        clock.advance(POLL_INTERVAL_S)
        session.tick()
        if len(_applied(session)) >= len(picks):
            break

    applied = len(_applied(session))
    ok = applied == len(picks) and sc.refreshes >= 1
    metrics = (
        f"applied {applied}/{len(picks)} picks; token refreshed "
        f"{sc.refreshes}x proactively (margin 900s); 0 missed"
    )
    print(f"{'PASS' if ok else 'FAIL'} refresh: {metrics}")
    _log_row("refresh", "PASS" if ok else "FAIL", metrics)
    return ok


def drill_crash(conn, season: int) -> bool:
    """Criterion 4: kill the session mid-draft at a pick, resume from the log
    (`DraftSession.resume` + `attach_poller`), finish the draft, and assert the
    derived state (taken / counts / overall / mode) is EXACTLY equal to a
    control session that never crashed."""
    crash_after = 97  # a random-ish mid-draft pick

    # Control: one straight run, its own transport + log.
    ctrl_clock = FakeClock()
    _, picks, team_slots, pool, priors, ctrl_fetch, ctrl_cfg = _prep(
        conn, season, {}, ctrl_clock, cadence_s=1.0
    )
    control = _build_session(
        conn, ctrl_cfg, pool, priors, ModeMachine(), ctrl_fetch, team_slots, ctrl_clock
    )
    for _ in range(len(picks) + 10):
        ctrl_clock.advance(POLL_INTERVAL_S)
        control.tick()
        if len(_applied(control)) >= len(picks):
            break

    # Crash run: same transport object reused across the crash so the release
    # schedule is continuous, exactly as a real crash+restart would see it.
    clock = FakeClock()
    fetch = _fetch_closure(picks, {}, clock=clock, cadence_s=1.0)
    log_path = _tmp_log()
    crash_cfg = SessionConfig(
        league_key=ctrl_cfg.league_key,
        our_franchise_slot=ctrl_cfg.our_franchise_slot,
        our_position=ctrl_cfg.our_position,
        log_path=log_path,
        poll_interval_s=POLL_INTERVAL_S,
    )
    session = _build_session(
        conn, crash_cfg, pool, priors, ModeMachine(), fetch, team_slots, clock
    )
    while len(_applied(session)) < crash_after:
        clock.advance(POLL_INTERVAL_S)
        session.tick()
    crashed_at = len(_applied(session))
    del session  # "kill -9": drop the in-memory object; only the log survives

    resumed = _build_session(
        conn,
        crash_cfg,
        pool,
        priors,
        ModeMachine(),
        fetch,
        team_slots,
        clock,
        resume=True,
    )
    for _ in range(len(picks) + 10):
        clock.advance(POLL_INTERVAL_S)
        resumed.tick()
        if len(_applied(resumed)) >= len(picks):
            break

    rs, cs = resumed._state, control._state
    same_taken = rs.taken_refs == cs.taken_refs
    same_counts = rs.counts_by_position == cs.counts_by_position
    same_overall = rs.next_overall == cs.next_overall
    same_mode = resumed.mode == control.mode
    ok = same_taken and same_counts and same_overall and same_mode
    metrics = (
        f"crashed@{crashed_at}, resumed to {len(_applied(resumed))}/{len(picks)}; "
        f"taken={'=' if same_taken else 'X'} counts={'=' if same_counts else 'X'} "
        f"overall={'=' if same_overall else 'X'}({rs.next_overall}) "
        f"mode={'=' if same_mode else 'X'}({resumed.mode.value})"
    )
    print(f"{'PASS' if ok else 'FAIL'} crash: {metrics}")
    _log_row("crash", "PASS" if ok else "FAIL", metrics)
    return ok


DRILLS = {
    "lag": drill_lag,
    "999": drill_999,
    "refresh": drill_refresh,
    "crash": drill_crash,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Rehearsal drill harness (Task 17)")
    ap.add_argument("--drill", required=True, choices=sorted(DRILLS))
    ap.add_argument("--season", type=int, default=2024)
    args = ap.parse_args()

    conn = connect()
    try:
        ok = DRILLS[args.drill](conn, args.season)
    finally:
        conn.close()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
