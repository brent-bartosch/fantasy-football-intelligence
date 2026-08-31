import pytest

from ffi.db import connect
from ffi.joblock import JobLockTimeout, acquire_or_wait, advisory_lock, lock_key


def _second_conn():
    """A genuinely separate session — advisory locks are per-connection, so a
    second holder is the only way to observe contention.

    Goes through `ffi.db.connect` rather than raw `psycopg2.connect` so these
    tests pick up the same DB_USER/DB_HOST/DB_PORT env the application uses;
    a bare psycopg2 call silently defaults to the OS user and would fail on any
    machine where that is not the Postgres role.
    """
    return connect("fantasy_football_test")


def test_lock_key_is_stable_and_in_range():
    a = lock_key("ffi.morning_chain")
    assert a == lock_key("ffi.morning_chain")
    assert a != lock_key("ffi.trends")
    assert -(2**63) <= a < 2**63


def test_acquire_succeeds_when_uncontended(db):
    acquire_or_wait(db, "test.uncontended", wait_s=1)


def test_lock_survives_a_commit_mid_hold(db):
    """The whole reason for session-level (not xact-level) advisory locks.

    build_valuation.py commits its DELETE+INSERT in the middle of the window it
    is serializing. If the lock were `pg_try_advisory_xact_lock`, that commit
    would drop it and the briefing could start reading half-written rows — the
    exact R22 failure, reintroduced invisibly. Assert the hold outlives commit
    from a second session's point of view, not just by re-taking it ourselves
    (a session can re-acquire its own advisory lock regardless).
    """
    acquire_or_wait(db, "test.commit_survival", wait_s=1)
    db.commit()
    other = _second_conn()
    try:
        with pytest.raises(JobLockTimeout, match="test.commit_survival"):
            acquire_or_wait(other, "test.commit_survival", wait_s=0.3, poll_s=0.05)
    finally:
        other.close()


def test_second_holder_times_out_loudly(db):
    acquire_or_wait(db, "test.contended", wait_s=1)
    other = _second_conn()
    try:
        with pytest.raises(JobLockTimeout, match="test.contended"):
            acquire_or_wait(other, "test.contended", wait_s=1, poll_s=0.1)
    finally:
        other.close()


def test_context_manager_releases_on_exit(db):
    with advisory_lock(db, "test.ctx", wait_s=1):
        pass
    other = _second_conn()
    try:
        acquire_or_wait(other, "test.ctx", wait_s=1, poll_s=0.1)
    finally:
        other.close()


def test_context_manager_releases_on_exception(db):
    with pytest.raises(RuntimeError, match="boom"):
        with advisory_lock(db, "test.ctx_exc", wait_s=1):
            raise RuntimeError("boom")
    other = _second_conn()
    try:
        acquire_or_wait(other, "test.ctx_exc", wait_s=1, poll_s=0.1)
    finally:
        other.close()
