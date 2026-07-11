"""ADR Domain 1 draft-day mode state machine: LIVE -> POLL-DEGRADED -> MANUAL
-> PAPER, with automatic downshift on poll failure / rate-limit and
operator-only recovery (mid-draft flapping between MANUAL and LIVE is worse
than staying manual). Pure logic, no I/O: the caller (Task 13's session)
logs every non-None transition to the DraftLog and renders the mode
banner -- this module never touches disk, network, or stdout.

Transition table (the spec; see .superpowers/sdd/task-10-brief.md):
  LIVE            + poll failure -> POLL-DEGRADED  (one failure, immediately)
  POLL-DEGRADED   + poll failure -> MANUAL          (two consecutive failures)
  LIVE/DEGRADED   + 999          -> MANUAL          (immediate, no retry)
  POLL-DEGRADED   + poll success -> LIVE            (auto-recover, resets counter)
  MANUAL/PAPER    + any poll event -> unchanged     (sticky; operator_set only)
  any             + operator_set(X) -> X            (incl. PAPER, MANUAL->LIVE)
"""
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    LIVE = "LIVE"
    POLL_DEGRADED = "POLL-DEGRADED"
    MANUAL = "MANUAL"
    PAPER = "PAPER"


_STICKY = (Mode.MANUAL, Mode.PAPER)


@dataclass
class ModeMachine:
    mode: Mode = Mode.LIVE
    consecutive_failures: int = 0

    def on_poll_success(self) -> tuple[Mode, str | None]:
        if self.mode in _STICKY:
            return self.mode, None
        self.consecutive_failures = 0
        if self.mode is Mode.POLL_DEGRADED:
            self.mode = Mode.LIVE
            return self.mode, "poll recovered -> LIVE"
        return self.mode, None

    def on_poll_failure(self) -> tuple[Mode, str | None]:
        if self.mode in _STICKY:
            return self.mode, None
        self.consecutive_failures += 1
        if self.mode is Mode.LIVE:
            self.mode = Mode.POLL_DEGRADED
            return (
                self.mode,
                f"poll failure #{self.consecutive_failures} -> POLL-DEGRADED",
            )
        self.mode = Mode.MANUAL
        return self.mode, "second consecutive poll failure -> MANUAL"

    def on_rate_limit(self) -> tuple[Mode, str | None]:
        if self.mode in _STICKY:
            return self.mode, None
        self.consecutive_failures += 1
        self.mode = Mode.MANUAL
        return self.mode, "error 999 (rate-limit lockout) -> MANUAL, no retry"

    def operator_set(self, target: Mode, reason: str) -> tuple[Mode, str]:
        self.mode = target
        self.consecutive_failures = 0
        return self.mode, f"operator: {reason}"
