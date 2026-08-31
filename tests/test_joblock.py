import psycopg2
import pytest

from ffi.joblock import JobLockTimeout, acquire_or_wait, advisory_lock, lock_key


def _second_conn():
    return psycopg2.connect(dbname="fantasy_football_test", host="localhost")


def test_lock_key_is_stable_and_in_range():
    a = lock_key("ffi.morning_chain")
    assert a == lock_key("ffi.morning_chain")
    assert a != lock_key("ffi.trends")
    assert -(2**63) <= a < 2**63


def test_acquire_succeeds_when_uncontended(db):
    acquire_or_wait(db, "test.uncontended", wait_s=1)


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
