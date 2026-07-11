"""Tests for the draft-day assistant's headless core (Phase 4 / Task 13).

Everything here drives `DraftSession` without a terminal: fake `fetch_fn`s
(the list-of-payload pattern Task 11's poller tests use) and a fake clock, so
every draft-day behavior -- poll/apply, the mode-degradation ladder, manual
picks with fuzzy match, undo, resume-from-log, and the our-turn forecast --
is asserted in isolation. No DB, no Yahoo, no real time.
"""
from pathlib import Path

import numpy as np
import pytest

from ffi.draft.modes import Mode, ModeMachine
from ffi.draft.poller import DraftPoller
from ffi.draft.session import AmbiguousPickError, DraftSession, SessionConfig
from ffi.draft.state import DraftLog
from ffi.sim.draft import ROUNDS, TEAMS
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import POSITIONS, SlotPriors
from ffi.sim.strategy import StrategyParams
from ffi.yahoo_client import YahooAuthError, YahooRateLimitError

# --------------------------------------------------------------------------
# Fixtures: synthetic pool / priors / clock / poller
# --------------------------------------------------------------------------

# team_key -> franchise_slot for a full 12-team league (identity: slot == the
# t.N number), matching the poller's team_slots contract.
TEAM_SLOTS = {f"461.l.1.t.{s}": s for s in range(1, TEAMS + 1)}


def _pool(named=None) -> list[PoolPlayer]:
    """~20 players per position, VORP-descending and ADP-ascending within a
    position, tiers in blocks of 4. `named` optionally injects specific
    (position, name) players at the head of a position for fuzzy-match tests."""
    players = []
    named = named or {}
    for pos in POSITIONS:
        inject = named.get(pos, [])
        for i in range(20):
            if i < len(inject):
                name = inject[i]
                ref = f"{pos}-{name.replace(' ', '_')}"
            else:
                name = f"{pos} Player {i}"
                ref = f"{pos}{i}"
            proj = 300.0 - i * 8.0 - POSITIONS.index(pos)
            players.append(
                PoolPlayer(
                    ref=ref,
                    name=name,
                    position=pos,
                    proj_points=proj,
                    vorp=proj - 100.0,
                    tier=1 + i // 4,
                    adp=float(POSITIONS.index(pos) * 20 + i + 1),
                    gsis_id=None,
                )
            )
    return players


def _priors() -> SlotPriors:
    """Uniform position shares for every (slot, round) -- enough for
    opponent_pick's rollouts in the forecast test; the exact distribution
    isn't under test here."""
    share = {pos: 1.0 / len(POSITIONS) for pos in POSITIONS}
    pos_share = {
        (slot, rnd): dict(share)
        for slot in range(1, TEAMS + 1)
        for rnd in range(1, ROUNDS + 1)
    }
    return SlotPriors(latest_season=2025, pos_share=pos_share, params={})


class FakeClock:
    """Monotonic-ish clock the test drives by hand."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _cfg(tmp_path: Path, our_position=1, our_slot=1, **kw) -> SessionConfig:
    return SessionConfig(
        league_key="461.l.1",
        our_franchise_slot=our_slot,
        our_position=our_position,
        log_path=tmp_path / "draft.jsonl",
        **kw,
    )


def _pick_payload(overall, round_, team_key, player_id):
    return {
        "pick": overall,
        "round": round_,
        "team_key": team_key,
        "player_id": player_id,
    }


def _resolver(table):
    def resolve(yahoo_player_id):
        return table.get(str(yahoo_player_id))

    return resolve


def _session(cfg, pool, priors, poller, machine, clock):
    return DraftSession(
        cfg, pool, priors, poller, machine, DraftLog(cfg.log_path), clock
    )


# --------------------------------------------------------------------------
# 1. tick applies new picks and advances the board
# --------------------------------------------------------------------------


def test_tick_applies_new_picks_and_advances_board(tmp_path):
    pool = _pool()
    cfg = _cfg(tmp_path)
    clock = FakeClock()
    # One made pick: overall 1, team t.1, resolves to the pool's QB0.
    picks = [_pick_payload(1, 1, "461.l.1.t.1", "yp1")]
    resolve = _resolver({"yp1": ("QB0", "QB Player 0", "QB")})
    log = DraftLog(cfg.log_path)
    poller = DraftPoller(lambda: picks, resolve, TEAM_SLOTS, log)
    session = DraftSession(cfg, pool, _priors(), poller, ModeMachine(), log, clock)

    # Board shows QB0 before the pick lands.
    assert any("QB Player 0" in ln for ln in session.board_lines("QB"))

    banners = session.tick()

    assert any("QB Player 0" in b for b in banners)
    # QB0 is now off the board; overall clock advanced to pick 2.
    assert not any("QB Player 0" in ln for ln in session.board_lines("QB"))
    assert session.on_the_clock_overall() == 2


# --------------------------------------------------------------------------
# 2. error 999 -> MANUAL and stays; operator flip back to LIVE resumes polling
# --------------------------------------------------------------------------


def test_999_goes_manual_and_stays(tmp_path):
    pool = _pool()
    cfg = _cfg(tmp_path)
    clock = FakeClock()

    state = {"raise_999": True, "picks": [_pick_payload(1, 1, "461.l.1.t.1", "yp1")]}

    def fetch():
        if state["raise_999"]:
            raise YahooRateLimitError("999")
        return state["picks"]

    resolve = _resolver({"yp1": ("QB0", "QB Player 0", "QB")})
    log = DraftLog(cfg.log_path)
    poller = DraftPoller(fetch, resolve, TEAM_SLOTS, log)
    machine = ModeMachine()
    session = DraftSession(cfg, pool, _priors(), poller, machine, log, clock)

    session.tick()
    assert machine.mode is Mode.MANUAL

    # Stays MANUAL and does NOT poll again even though fetch would now succeed.
    state["raise_999"] = False
    clock.advance(100)
    banners = session.tick()
    assert machine.mode is Mode.MANUAL
    assert banners == []  # sticky: no poll attempted in MANUAL

    # Operator flips back to LIVE -> polling resumes, pick applied.
    session.set_mode("live")
    clock.advance(100)
    session.tick()
    assert machine.mode is Mode.LIVE
    assert not any("QB Player 0" in ln for ln in session.board_lines("QB"))


# --------------------------------------------------------------------------
# 3. two consecutive poll failures -> POLL-DEGRADED -> MANUAL
# --------------------------------------------------------------------------


def test_two_failures_go_manual(tmp_path):
    pool = _pool()
    cfg = _cfg(tmp_path)
    clock = FakeClock()

    def fetch():
        raise YahooAuthError("token dead")

    log = DraftLog(cfg.log_path)
    poller = DraftPoller(fetch, _resolver({}), TEAM_SLOTS, log)
    machine = ModeMachine()
    session = DraftSession(cfg, pool, _priors(), poller, machine, log, clock)

    session.tick()
    assert machine.mode is Mode.POLL_DEGRADED
    clock.advance(100)
    session.tick()
    assert machine.mode is Mode.MANUAL


# --------------------------------------------------------------------------
# 4. manual pick: fuzzy match + ambiguity (never guess)
# --------------------------------------------------------------------------


def test_manual_pick_fuzzy_match_and_ambiguity(tmp_path):
    pool = _pool(named={"QB": ["Josh Allen", "Josh Jacobs"]})
    cfg = _cfg(tmp_path, our_position=1, our_slot=1)
    clock = FakeClock()
    session = _session(cfg, pool, _priors(), None, ModeMachine(mode=Mode.MANUAL), clock)

    # Ambiguous prefix matches both Joshes -> raises, listing candidates.
    with pytest.raises(AmbiguousPickError) as exc:
        session.manual_pick("jos")
    assert "Josh Allen" in str(exc.value) and "Josh Jacobs" in str(exc.value)

    # No match -> a plain error, nothing guessed.
    with pytest.raises(ValueError):
        session.manual_pick("zzzz nobody")

    # Unique multi-token match -> Josh Allen.
    picked = session.manual_pick("jos all")
    assert picked.name == "Josh Allen"
    # Off the board now.
    assert not any("Josh Allen" in ln for ln in session.board_lines("QB"))


# --------------------------------------------------------------------------
# 5. undo rebuilds state
# --------------------------------------------------------------------------


def test_undo_rebuilds_state(tmp_path):
    pool = _pool(named={"QB": ["Josh Allen"]})
    cfg = _cfg(tmp_path, our_position=1, our_slot=1)
    clock = FakeClock()
    session = _session(cfg, pool, _priors(), None, ModeMachine(mode=Mode.MANUAL), clock)

    session.manual_pick("josh allen")
    assert session.on_the_clock_overall() == 2
    assert not any("Josh Allen" in ln for ln in session.board_lines("QB"))

    session.undo_last()
    assert session.on_the_clock_overall() == 1
    assert any("Josh Allen" in ln for ln in session.board_lines("QB"))


# --------------------------------------------------------------------------
# 6. resume reproduces state from the log
# --------------------------------------------------------------------------


def test_resume_reproduces_state(tmp_path):
    pool = _pool(
        named={
            "QB": ["Josh Allen"],
            "RB": ["Bijan Robinson"],
            "WR": ["Ceedee Lamb"],
            "TE": ["Mark Andrews"],
        }
    )
    cfg = _cfg(tmp_path, our_position=1, our_slot=1)
    clock = FakeClock()

    session = _session(cfg, pool, _priors(), None, ModeMachine(mode=Mode.MANUAL), clock)
    session.manual_pick("josh allen")  # overall 1
    session.manual_pick("bijan")  # overall 2
    session.manual_pick("ceedee")  # overall 3
    session.set_mode("paper")  # a mode event mid-stream
    session.manual_pick("andrews")  # overall 4

    before_board = session.board_lines()
    before_clock = session.on_the_clock_overall()
    before_mode = session.mode

    # "Crash": drop the object, resume purely from the log file.
    resumed = DraftSession.resume(cfg, pool, _priors(), None, ModeMachine())

    assert resumed.on_the_clock_overall() == before_clock
    assert resumed.board_lines() == before_board
    assert resumed.mode is before_mode is Mode.PAPER


# --------------------------------------------------------------------------
# 7. our-turn recommendation uses the forecast
# --------------------------------------------------------------------------


def test_our_turn_recommendation_uses_forecast(tmp_path):
    pool = _pool()
    # our_position/slot 12: picks at overall 12 (R1) then 13 (R2, back-to-back),
    # so after a full round 1 is polled the clock sits on OUR pick with the
    # slot map complete and ~22 opponent picks before our next turn.
    cfg = _cfg(tmp_path, our_position=12, our_slot=12, forecast_rollouts=25)
    clock = FakeClock()

    # A full round 1: overall o at draft position o (round 1 ascending),
    # franchise_slot == position (identity), each a distinct resolvable player.
    table = {}
    r1 = []
    for o in range(1, TEAMS + 1):
        pos = POSITIONS[o % len(POSITIONS)]
        ref = f"{pos}{o % 20}"
        table[f"yp{o}"] = (ref, f"{pos} Player {o % 20}", pos)
        r1.append(_pick_payload(o, 1, f"461.l.1.t.{o}", f"yp{o}"))

    log = DraftLog(cfg.log_path)
    poller = DraftPoller(lambda: r1, _resolver(table), TEAM_SLOTS, log)
    session = DraftSession(cfg, pool, _priors(), poller, ModeMachine(), log, clock)

    session.tick()  # applies all of round 1

    assert session.on_the_clock_overall() == 13  # our R2 pick
    rec = session.recommendation()
    assert rec.vona is not None  # forecast attached -> VONA computed
    assert rec.primary is not None


# --------------------------------------------------------------------------
# 8. tick respects the poll interval (no double polls)
# --------------------------------------------------------------------------


def test_tick_respects_poll_interval(tmp_path):
    pool = _pool()
    cfg = _cfg(tmp_path, poll_interval_s=7.0)
    clock = FakeClock()

    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return []

    log = DraftLog(cfg.log_path)
    poller = DraftPoller(fetch, _resolver({}), TEAM_SLOTS, log)
    session = DraftSession(cfg, pool, _priors(), poller, ModeMachine(), log, clock)

    session.tick()  # first tick polls
    assert calls["n"] == 1
    session.tick()  # same instant -> within interval, no poll
    assert calls["n"] == 1
    clock.advance(7.0)  # interval elapsed
    session.tick()
    assert calls["n"] == 2
