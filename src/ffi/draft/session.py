"""Draft-day assistant: the headless `DraftSession` core (Phase 4 / Task 13).

`DraftSession` composes everything Tasks 8-12 built into one drillable object:
the poller (Task 11) feeds picks, the mode machine (Task 10) governs
degradation, the event log (Task 9) is the durable source of truth, and the
recommendation engine (Task 12) + VONA forecast (Task 8) produce the operator's
between-picks view. The terminal shell (`scripts/draft_assistant.py`) is a dumb
renderer over this core -- so every behavior (poll/apply, the LIVE ->
POLL-DEGRADED -> MANUAL ladder, manual fuzzy picks, undo, resume-from-log, the
our-turn forecast) is testable and rehearsable without a terminal.

Failure policy: `tick()` owns the ONLY try/except on the poll path -- exactly
the two typed Yahoo errors, each mapping to a mode transition (ADR Domain 1).
Everything else fails loud: a draft-order contradiction, an undo of a polled
pick, a fuzzy miss/ambiguity, a torn log tail -- all raise or banner, never
silently degrade. State-derivation lives in `ffi.draft.replay` (the pure half
Task 16 imports); this module adds the I/O around it.
"""
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ffi.draft.modes import Mode, ModeMachine
from ffi.draft.poller import DraftPoller, ResolvedPick
from ffi.draft.recommend import Recommendation, recommend
from ffi.draft.replay import derive_state
from ffi.draft.state import DraftLog
from ffi.sim.availability import AvailabilityForecast, forecast_availability
from ffi.sim.draft import (
    ROUNDS,
    TOTAL_PICKS,
    _avail_view,
    _build_sorted_pool,
    snake_position,
)
from ffi.sim.pool import PoolPlayer
from ffi.sim.strategy import StrategyParams
from ffi.yahoo_client import YahooAuthError, YahooRateLimitError


class AmbiguousPickError(Exception):
    """A fuzzy `p <name>` query matched more than one available player. The
    assistant never guesses which -- it lists the candidates and refuses."""

    def __init__(self, query: str, candidates):
        self.query = query
        self.candidates = list(candidates)
        names = ", ".join(f"{c.name} ({c.position})" for c in self.candidates)
        super().__init__(f"{query!r} matches {len(self.candidates)} players: {names}")


@dataclass
class SessionConfig:
    league_key: str
    our_franchise_slot: int
    our_position: int
    scenario: str = "qb_hoard_12"
    poll_interval_s: float = 7.0  # ADR: 5-10s; tune from rehearsal lag data
    log_path: Path | None = None  # default: data/draft-logs/<date>-<league_key>.jsonl
    params: StrategyParams = field(default_factory=StrategyParams)
    forecast_rollouts: int = 200
    forecast_seed: int = 20260710
    forecast_max_upcoming: int = 30  # skip the forecast past this many opp. picks
    board_vintage: dict | None = None  # ADR D2 stamp, recorded in the meta event

    def __post_init__(self):
        if self.log_path is None:
            self.log_path = (
                Path("data/draft-logs")
                / f"{date.today().isoformat()}-{self.league_key}.jsonl"
            )
        self.log_path = Path(self.log_path)


_MODE_WORDS = {"live": Mode.LIVE, "manual": Mode.MANUAL, "paper": Mode.PAPER}

# Task 16 (advisory agent lane, EXPENDABLE per risk R3): a separate OS process
# tails the log and writes this file atomically after each opponent pick. It
# is rendered here ONLY when fresh (mtime < this threshold) -- a dead or
# absent lane degrades to one disclosure line, never a crash; the lane can be
# killed at any point in a draft with zero effect on the board.
AGENT_LANE_PATH = Path("reports/draft-annotations-live.md")
AGENT_LANE_STALE_S = 300


class DraftSession:
    def __init__(
        self,
        cfg: SessionConfig,
        pool: list,
        priors,
        poller: DraftPoller | None,
        machine: ModeMachine,
        log: DraftLog,
        clock=time.monotonic,
        _resuming: bool = False,
    ):
        self.cfg = cfg
        self.pool = pool
        self.priors = priors
        self.poller = poller
        self.machine = machine
        self.log = log
        self.clock = clock

        self._sorted_pool = _build_sorted_pool(pool)
        self._events: list[tuple[str, dict]] = []
        self._meta: dict | None = None
        # `_replayed_events` / `_torn_tail` are DraftLog's parse output (Task 9):
        # a fresh log created moments ago has none, a replayed one has the full
        # history. Reading them here unifies fresh-start and resume in one path.
        self.torn_tail = bool(getattr(self.log, "_torn_tail", False))
        existing = list(getattr(self.log, "_replayed_events", []))

        if not existing:
            self._append_meta()  # META-FIRST is the session's convention to enforce
        else:
            # A fresh construction must never open onto an already-populated
            # log: it would replay someone else's draft, and (worse) a second
            # DraftLog handle would race the poller's _next_seq and corrupt the
            # file. Reconstructing a populated log is `resume()`'s job alone.
            if not _resuming:
                raise ValueError(
                    f"log {self.cfg.log_path} already has {len(existing)} event(s) "
                    "-- refusing a fresh start on a non-empty log. Pass --resume to "
                    "continue this draft, or --log-path to start a new one."
                )
            if existing[0].kind != "meta":
                raise ValueError(
                    f"log {self.cfg.log_path} does not start with a meta event "
                    "(META-FIRST violated) -- refusing to resume a malformed log"
                )
            self._meta = existing[0].payload
            self._events = [
                (e.kind, e.payload) for e in existing if e.kind in ("pick", "undo")
            ]
            mode_events = [e for e in existing if e.kind == "mode"]
            if mode_events:
                self.machine.mode = Mode(mode_events[-1].payload["mode"])

        self._last_poll = self.clock() - self.cfg.poll_interval_s
        self._rebuild()

    # -- construction / persistence -----------------------------------------

    def _append_meta(self) -> None:
        payload = {
            "league_key": self.cfg.league_key,
            "our_franchise_slot": self.cfg.our_franchise_slot,
            "our_position": self.cfg.our_position,
            "scenario": self.cfg.scenario,
            "board_vintage": self.cfg.board_vintage,
        }
        self.log.append("meta", payload)
        self._meta = payload

    @classmethod
    def resume(cls, cfg, pool, priors, machine, clock=time.monotonic) -> "DraftSession":
        """Rebuild a session from its on-disk log (replay). The session OWNS
        the single DraftLog handle this returns (`session.log`); state, mode,
        and the torn-tail flag all come from it, and `machine`'s incoming mode
        is overridden by the log's last mode event.

        A live poller is NOT passed here -- it must be built from `session.log`
        and attached via `attach_poller`, so exactly one DraftLog handle (one
        `_next_seq` counter) ever writes the file. Two handles would race and
        corrupt the seq sequence, which the next resume refuses to load."""
        log, _events, _torn = DraftLog.replay(cfg.log_path)
        return cls(cfg, pool, priors, None, machine, log, clock, _resuming=True)

    def attach_poller(self, poller: DraftPoller) -> None:
        """Attach the live poller after construction, seeding its dedupe set
        from picks already durable in the log so an attached/resumed poll never
        re-logs them (the poller starts with an empty `_seen`; Task 11 leaves
        seeding to the session).

        Contract: `poller` MUST have been built from THIS session's `self.log`.
        One DraftLog handle per process -- a poller wired to a second handle
        races this session's `_next_seq` and corrupts the log."""
        self.poller = poller
        poller._seen = {
            o
            for o, p in self._state.picks_by_overall.items()
            if p.get("source") == "poll"
        }

    def _rebuild(self) -> None:
        self._state = derive_state(
            self._events, self.cfg.our_franchise_slot, self.cfg.our_position
        )

    # -- the loop tick (owns the only poll-path try/except) ------------------

    def tick(self) -> list[str]:
        """One loop iteration: maybe poll, apply any new picks, transition the
        mode machine. Returns banner lines for the shell to render."""
        banners: list[str] = []
        if (
            self.poller is not None
            and self.machine.mode in (Mode.LIVE, Mode.POLL_DEGRADED)
            and self.clock() - self._last_poll >= self.cfg.poll_interval_s
        ):
            try:
                result = self.poller.poll()
            except YahooRateLimitError:
                (
                    new,
                    reason,
                ) = self.machine.on_rate_limit()  # -> MANUAL, no retry (ADR D1)
                banners += self._log_mode(new, reason)
            except YahooAuthError as e:
                new, reason = self.machine.on_poll_failure()
                banners += self._log_mode(new, reason, detail=str(e))
            else:
                banners += self._apply(result.new_picks)
                new, reason = self.machine.on_poll_success()
                banners += self._log_mode(new, reason)
            self._last_poll = self.clock()
        # FAIL-LOUD Level 2: in POLL-DEGRADED/MANUAL the board serves last-known draft
        # state — degraded state is disclosed via the mode banner rendered on EVERY
        # frame plus a logged "mode" event; recovery to LIVE is automatic only from
        # POLL-DEGRADED (ADR Domain 1). Any exception other than the two typed Yahoo
        # errors propagates and crashes: state is fsync'd per pick, resume is drilled.
        return banners

    def _log_mode(
        self, new: Mode, reason: str | None, detail: str | None = None
    ) -> list[str]:
        if reason is None:
            return []
        payload = {"mode": new.value, "reason": reason}
        if detail is not None:
            payload["detail"] = detail
        self.log.append("mode", payload)
        return [f"[MODE] {new.value}: {reason}"]

    def _apply(self, new_picks) -> list[str]:
        """Mirror poller picks into the state event stream. The poller has
        ALREADY logged them (durable-before-visible) -- we only fold them into
        derived state and banner them, never double-log."""
        banners: list[str] = []
        for rp in new_picks:
            payload = {
                "overall": rp.overall,
                "round": rp.round,
                "franchise_slot": rp.franchise_slot,
                "team_key": rp.team_key,
                "ref": rp.ref,
                "yahoo_player_id": rp.yahoo_player_id,
                "name": rp.name,
                "pos": rp.pos,
                "source": "poll",
            }
            self._events.append(("pick", payload))
            if rp.ref is None:
                banners.append(
                    f"[CROSSWALK MISS] overall {rp.overall}: yahoo id "
                    f"{rp.yahoo_player_id} did not resolve — identify manually "
                    "before trusting the board (never guessed)"
                )
            else:
                banners.append(
                    f"pick {rp.overall}: {rp.name} ({rp.pos}) -> slot {rp.franchise_slot}"
                )
        self._rebuild()  # re-runs the draft-order cross-check (fail-loud on contradiction)
        return banners

    # -- operator actions ----------------------------------------------------

    def set_mode(self, word: str, reason: str | None = None) -> list[str]:
        """Operator mode set (`m live|manual|paper`). Recovery to LIVE is the
        only way out of MANUAL (the machine refuses automatic un-sticking)."""
        target = _MODE_WORDS.get(word.lower())
        if target is None:
            raise ValueError(
                f"unknown mode {word!r} -- expected one of {sorted(_MODE_WORDS)}"
            )
        new, out = self.machine.operator_set(target, reason or f"set {target.value}")
        return self._log_mode(new, out)

    def manual_pick(self, query: str) -> ResolvedPick:
        """Fuzzy-resolve `query` against AVAILABLE players and record it as the
        pick for the seat currently on the clock (source="manual")."""
        st = self._state
        overall = st.next_overall
        if len(st.picks_by_overall) >= TOTAL_PICKS:
            raise ValueError("draft is complete -- no seat is on the clock")
        rnd, position = snake_position(overall)
        player = self._fuzzy_find(query)

        if position == self.cfg.our_position:
            slot = self.cfg.our_franchise_slot
        else:
            slot = st.slot_of_position.get(position)  # None if seat's slot unknown

        payload = {
            "overall": overall,
            "round": rnd,
            "franchise_slot": slot,
            "team_key": None,
            "ref": player.ref,
            "yahoo_player_id": None,
            "name": player.name,
            "pos": player.position,
            "source": "manual",
        }
        self.log.append("pick", payload)
        self._events.append(("pick", payload))
        self._rebuild()
        return ResolvedPick(
            overall=overall,
            round=rnd,
            team_key=None,
            franchise_slot=slot if slot is not None else 0,  # 0 = seat unknown
            yahoo_player_id=None,
            ref=player.ref,
            name=player.name,
            pos=player.position,
        )

    def undo_last(self) -> None:
        """Undo the most recent pick -- MANUAL picks only. Undoing a polled
        pick locally would desync from Yahoo, so it is refused (ADR Domain 1)."""
        st = self._state
        if not st.picks_by_overall:
            raise ValueError("nothing to undo")
        last_overall = max(st.picks_by_overall)
        last = st.picks_by_overall[last_overall]
        if last.get("source") != "manual":
            raise ValueError(
                f"refusing to undo overall {last_overall}: it is a "
                f"{last.get('source')!r} (polled) pick; undoing it locally would "
                "desync from Yahoo. Switch to mode MANUAL to take over the board."
            )
        payload = {"overall": last_overall}
        self.log.append("undo", payload)
        self._events.append(("undo", payload))
        self._rebuild()

    def _fuzzy_find(self, query: str) -> PoolPlayer:
        tokens = query.lower().split()
        if not tokens:
            raise ValueError("empty pick query")
        taken = set(self._state.taken_refs)
        matches = [
            p
            for p in self.pool
            if p.ref not in taken and all(tok in p.name.lower() for tok in tokens)
        ]
        if not matches:
            raise ValueError(f"no available player matches {query!r}")
        if len(matches) > 1:
            raise AmbiguousPickError(query, matches)
        return matches[0]

    # -- views (dumb-renderer surface) --------------------------------------

    @property
    def mode(self) -> Mode:
        return self.machine.mode

    def on_the_clock_overall(self) -> int:
        return self._state.next_overall

    def board_lines(self, pos: str | None = None) -> list[str]:
        st = self._state
        avail = [p for p in self.pool if p.ref not in st.taken_refs]
        if pos is not None:
            pos = pos.upper()
            avail = [p for p in avail if p.position == pos]
            limit = 15
            lines = [f"Best available {pos}:"]
        else:
            limit = 30
            lines = ["Best available (overall):"]
        for p in avail[:limit]:
            adp = f"{p.adp:.0f}" if p.adp is not None else "—"
            lines.append(
                f"  {p.name:<24} {p.position:<3} tier {p.tier}  "
                f"vorp {p.vorp:6.1f}  adp {adp}"
            )
        return lines

    def status_lines(self) -> list[str]:
        st = self._state
        lines = [f"[MODE] {self.mode.value}"]
        if self.torn_tail:
            lines.append(
                "[TORN TAIL] the log's final line was truncated by a crash and "
                "dropped on resume — verify the last pick below matches Yahoo."
            )
        overall = st.next_overall
        rnd, position = snake_position(overall)
        whose = (
            "US" if position == self.cfg.our_position else f"draft position {position}"
        )
        lines.append(
            f"On the clock: overall {overall} (round {rnd}, position {position}) — {whose}"
        )
        our_counts = st.counts_by_position[self.cfg.our_position]
        roster = (
            ", ".join(f"{p}:{c}" for p, c in sorted(our_counts.items())) or "(empty)"
        )
        lines.append(f"Our roster ({sum(our_counts.values())} picks): {roster}")
        our_names = [
            p["name"]
            for o, p in sorted(st.picks_by_overall.items())
            if snake_position(o)[1] == self.cfg.our_position and p.get("name")
        ]
        if our_names:
            lines.append("  " + "; ".join(our_names))
        if st.unresolved:
            lines.append(
                f"[UNRESOLVED] {len(st.unresolved)} pick(s) not identified "
                f"(overalls {list(st.unresolved)}) — resolve before trusting "
                "opponent counts / forecast"
            )
        if self._meta and self._meta.get("board_vintage"):
            v = self._meta["board_vintage"]
            lines.append(
                f"[VINTAGE] ADP snapshot {v.get('adp_snapshot_id')} "
                f"({v.get('adp_age_hours')}h old), valuation "
                f"{v.get('valuation_snapshot_id')}"
            )
        lines += self._agent_lane_lines()
        return lines

    def _agent_lane_lines(self) -> list[str]:
        """Task 16's advisory lane content, rendered only when fresh -- see
        `AGENT_LANE_PATH`/`AGENT_LANE_STALE_S` above.

        FAIL-LOUD Level 2: the annotations file is written by a separate,
        expendable OS process this session doesn't control -- an unreadable
        file or a torn/invalid-UTF-8 write it caught mid-`os.replace` is
        degraded ADVISORY input, not a reason to crash the live assistant
        (the shell's command dispatcher deliberately lets everything else
        propagate). Disclosed in-banner as "[AGENT LANE] unreadable" instead;
        the pick path is entirely unaffected either way."""
        path = AGENT_LANE_PATH
        try:
            if not path.exists():
                return ["[AGENT LANE] stale/absent"]
            age_s = time.time() - path.stat().st_mtime
            if age_s >= AGENT_LANE_STALE_S:
                return ["[AGENT LANE] stale/absent"]
            return ["[AGENT LANE]"] + path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return ["[AGENT LANE] unreadable"]

    def recommendation(self) -> Recommendation:
        st = self._state
        our_next = self._our_next_overall()
        rnd, _ = snake_position(our_next)
        counts = dict(st.counts_by_position[self.cfg.our_position])
        picks_left_after = ROUNDS - rnd
        avail_by_pos = _avail_view(self._sorted_pool, set(st.taken_refs))
        forecast = self._maybe_forecast(avail_by_pos)
        return recommend(
            avail_by_pos,
            rnd,
            counts,
            picks_left_after,
            self.cfg.params,
            forecast=forecast,
        )

    def recommendation_lines(self) -> list[str]:
        """Render `recommendation()` respecting Task 12's critical rule: on a
        FORCE turn (rule != 'value') the primary is the recommendation and the
        `top` list is explicitly a 'value view', never presented as the pick."""
        rec = self.recommendation()
        p = rec.primary
        if rec.rule == "value":
            lines = [
                f"RECOMMEND: {p.name} ({p.position}) — best value, "
                f"vorp {p.vorp:.1f}, tier {p.tier}"
            ]
        else:
            lines = [
                f"RECOMMEND (FORCED by {rec.rule}): {p.name} ({p.position}) — "
                f"vorp {p.vorp:.1f}, tier {p.tier}",
                "  (the value view below is NOT the pick on a forced turn)",
            ]
        lines.append("Value view (top rule-4 candidates):")
        for score, cand in rec.top[:5]:
            lines.append(
                f"  {cand.name:<24} {cand.position:<3} "
                f"score {score:6.1f}  vorp {cand.vorp:6.1f}"
            )
        if rec.notes:
            lines.append("Notes:")
            lines += [f"  - {n}" for n in rec.notes]
        return lines

    # -- forecast wiring -----------------------------------------------------

    def _our_next_overall(self) -> int:
        o = self._state.next_overall
        while o < TOTAL_PICKS and snake_position(o)[1] != self.cfg.our_position:
            o += 1
        return o

    def _maybe_forecast(self, avail_by_pos) -> AvailabilityForecast | None:
        """Forecast availability at our NEXT pick -- only when we are actually
        on the clock (so `avail_by_pos` is our real decision point), the slot
        map covers every intervening seat, and the horizon is <= the configured
        cap. Otherwise return None (a visible no-forecast, VONA absent)."""
        st = self._state
        if snake_position(st.next_overall)[1] != self.cfg.our_position:
            return None  # previewing a future turn; board isn't at that point yet
        our_next = st.next_overall
        our_next2 = our_next + 1
        while (
            our_next2 <= TOTAL_PICKS
            and snake_position(our_next2)[1] != self.cfg.our_position
        ):
            our_next2 += 1
        upcoming_overalls = list(range(our_next + 1, our_next2))
        if len(upcoming_overalls) > self.cfg.forecast_max_upcoming:
            return None
        upcoming = []
        for o in upcoming_overalls:
            r, position = snake_position(o)
            slot = st.slot_of_position.get(position)
            if slot is None:
                return None  # slot map incomplete for this seat -> can't forecast
            upcoming.append((slot, r, st.counts_by_position[position]))
        return forecast_availability(
            avail_by_pos,
            self.priors,
            upcoming,
            n_rollouts=self.cfg.forecast_rollouts,
            seed=self.cfg.forecast_seed + our_next,
            opponent_params=None,  # calibrated DEFAULT_OPPONENT_PARAMS (Task 4)
        )


def write_paper_board(pool: list, path: Path) -> Path:
    """The PAPER floor: top 60 overall + top 15 per position with tiers,
    written before the draft room opens. Mode-independent (no taken filter) --
    it exists so the operator can draft from paper even if every online path
    dies. `pool` is already in draft order (real-ADP then undrafted-sentinel)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    lines = [f"# Paper board — {today}", ""]

    def _row(rank, p):
        adp = f"{p.adp:.0f}" if p.adp is not None else "—"
        return (
            f"{rank:>3}. {p.name:<24} {p.position:<3} tier {p.tier}  "
            f"vorp {p.vorp:6.1f}  adp {adp}"
        )

    lines.append("## Top 60 overall")
    lines.append("")
    for i, p in enumerate(pool[:60], start=1):
        lines.append(_row(i, p))
    lines.append("")

    by_pos: dict[str, list] = {}
    for p in pool:
        by_pos.setdefault(p.position, []).append(p)
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        plist = by_pos.get(pos, [])
        lines.append("")
        lines.append(f"## Top 15 {pos}")
        lines.append("")
        for i, p in enumerate(plist[:15], start=1):
            lines.append(_row(i, p))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
