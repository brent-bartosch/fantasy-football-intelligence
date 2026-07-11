"""ADR Domain 1 transition-table pins for the draft mode machine. Each test
name maps to one row of the table in .superpowers/sdd/task-10-brief.md --
if a table row's expected behavior ever changes, the corresponding test
name should change too, so a diff here is a visible ADR-D1 amendment."""
from ffi.draft.modes import Mode, ModeMachine


def test_live_poll_failure_goes_to_poll_degraded():
    m = ModeMachine()
    mode, reason = m.on_poll_failure()
    assert mode == Mode.POLL_DEGRADED
    assert m.mode == Mode.POLL_DEGRADED
    assert reason is not None


def test_poll_degraded_poll_failure_goes_to_manual():
    m = ModeMachine(mode=Mode.POLL_DEGRADED, consecutive_failures=1)
    mode, reason = m.on_poll_failure()
    assert mode == Mode.MANUAL
    assert m.mode == Mode.MANUAL
    assert reason is not None


def test_live_rate_limit_goes_to_manual():
    m = ModeMachine()
    mode, reason = m.on_rate_limit()
    assert mode == Mode.MANUAL
    assert m.mode == Mode.MANUAL
    assert reason is not None


def test_poll_degraded_rate_limit_goes_to_manual():
    m = ModeMachine(mode=Mode.POLL_DEGRADED, consecutive_failures=1)
    mode, reason = m.on_rate_limit()
    assert mode == Mode.MANUAL
    assert reason is not None


def test_poll_degraded_poll_success_recovers_to_live():
    m = ModeMachine(mode=Mode.POLL_DEGRADED, consecutive_failures=1)
    mode, reason = m.on_poll_success()
    assert mode == Mode.LIVE
    assert m.mode == Mode.LIVE
    assert reason is not None


def test_manual_poll_success_is_sticky():
    m = ModeMachine(mode=Mode.MANUAL, consecutive_failures=2)
    mode, reason = m.on_poll_success()
    assert mode == Mode.MANUAL
    assert m.mode == Mode.MANUAL
    assert reason is None


def test_manual_poll_failure_is_sticky():
    m = ModeMachine(mode=Mode.MANUAL, consecutive_failures=2)
    mode, reason = m.on_poll_failure()
    assert mode == Mode.MANUAL
    assert m.mode == Mode.MANUAL
    assert reason is None


def test_manual_rate_limit_is_sticky():
    m = ModeMachine(mode=Mode.MANUAL, consecutive_failures=2)
    mode, reason = m.on_rate_limit()
    assert mode == Mode.MANUAL
    assert m.mode == Mode.MANUAL
    assert reason is None


def test_operator_set_from_live_to_paper():
    m = ModeMachine()
    mode, reason = m.operator_set(Mode.PAPER, "assistant unusable, printed board")
    assert mode == Mode.PAPER
    assert m.mode == Mode.PAPER
    assert reason == "operator: assistant unusable, printed board"


def test_operator_set_from_manual_to_live():
    m = ModeMachine(mode=Mode.MANUAL, consecutive_failures=2)
    mode, reason = m.operator_set(Mode.LIVE, "lockout window has passed")
    assert mode == Mode.LIVE
    assert m.mode == Mode.LIVE
    assert reason == "operator: lockout window has passed"


# --- plus the two named extra tests from the brief ---


def test_counter_resets_on_success():
    m = ModeMachine()
    m.on_poll_failure()
    assert m.consecutive_failures == 1
    m.on_poll_success()
    assert m.mode == Mode.LIVE
    assert m.consecutive_failures == 0

    # After the reset, a fresh failure must be counted as failure #1 again,
    # not continue accumulating from the pre-recovery count.
    mode, reason = m.on_poll_failure()
    assert mode == Mode.POLL_DEGRADED
    assert reason == "poll failure #1 -> POLL-DEGRADED"


def test_rate_limit_from_live_skips_degraded():
    m = ModeMachine()
    mode, reason = m.on_rate_limit()
    assert mode == Mode.MANUAL
    assert m.mode == Mode.MANUAL
    assert m.mode != Mode.POLL_DEGRADED
    assert reason is not None


# --- reviewer-requested additions ---


def test_paper_poll_success_is_sticky():
    m = ModeMachine(mode=Mode.PAPER, consecutive_failures=2)
    mode, reason = m.on_poll_success()
    assert mode == Mode.PAPER
    assert m.mode == Mode.PAPER
    assert reason is None


def test_paper_poll_failure_is_sticky():
    m = ModeMachine(mode=Mode.PAPER, consecutive_failures=2)
    mode, reason = m.on_poll_failure()
    assert mode == Mode.PAPER
    assert m.mode == Mode.PAPER
    assert reason is None


def test_paper_rate_limit_is_sticky():
    m = ModeMachine(mode=Mode.PAPER, consecutive_failures=2)
    mode, reason = m.on_rate_limit()
    assert mode == Mode.PAPER
    assert m.mode == Mode.PAPER
    assert reason is None


def test_operator_set_from_manual_to_live_resets_counter():
    m = ModeMachine(mode=Mode.MANUAL, consecutive_failures=2)
    m.operator_set(Mode.LIVE, "lockout window has passed")
    assert m.consecutive_failures == 0


def test_operator_set_to_same_mode_still_returns_reason_and_resets_counter():
    # Deliberate choice: operator_set always returns a transition record
    # (Mode, str) -- never None -- even when target == current mode. The
    # caller may choose not to log a no-op transition; the machine itself
    # doesn't special-case it.
    m = ModeMachine(mode=Mode.LIVE, consecutive_failures=0)
    mode, reason = m.operator_set(Mode.LIVE, "re-confirming live")
    assert mode == Mode.LIVE
    assert reason == "operator: re-confirming live"
