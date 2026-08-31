"""Postgres advisory locks for job serialization (R22, ADR Domain 8).

The Tuesday trends job (12-45 min) overlaps com.ffi.morning at 07:00, which
runs build_valuation.py's DELETE+INSERT inside a single commit. A report
reading across that boundary is half-old and half-new with no error raised —
the "degraded-quality presence" class this whole ADR is organized around.

Advisory locks rather than a lockfile because they die with the connection:
a killed job cannot leave a stale lock that blocks every subsequent run, and
there is no cleanup path to forget.

`pg_try_advisory_lock` in a poll loop, never `pg_advisory_lock`: the
blocking form waits forever, which converts a deadlock into a silently
missed deadline. Timing out loudly is the whole point.

Layer 0 leaf: imports nothing internal.
"""
import contextlib
import hashlib
import time

DEFAULT_WAIT_S = 900.0
DEFAULT_POLL_S = 5.0


class JobLockTimeout(Exception):
    """Another process still holds the lock after `wait_s`."""


def lock_key(name: str) -> int:
    """Stable signed 64-bit key from a lock name.

    Postgres advisory locks take a bigint, and the key space is GLOBAL to the
    database — deriving it from a name means two jobs collide only if they
    were meant to.
    """
    digest = hashlib.sha256(name.encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def acquire_or_wait(
    conn,
    name: str,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
) -> None:
    """Take the session-level advisory lock `name`, polling until `wait_s`.

    Session-level (not transaction-level): the lock outlives commits, so a
    job that commits mid-run keeps its serialization. It is released by
    `release()` or by the connection closing — which for a script means
    process exit.
    """
    key = lock_key(name)
    deadline = time.monotonic() + wait_s
    attempts = 0
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            got = cur.fetchone()[0]
        if got:
            return
        # A failed probe still opened a transaction. Without this commit the
        # poller sits idle-in-transaction for the whole wait window, pinning the
        # xmin horizon and stalling autovacuum while a 45-min rebuild runs. Only
        # the failing path commits: on success the caller owns the transaction
        # (and the lock is session-level, so it survives commits regardless).
        conn.commit()
        attempts += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise JobLockTimeout(
                f"could not acquire advisory lock {name!r} (key {key}) after "
                f"{wait_s:.0f}s and {attempts} attempts — another job still holds it. "
                f"Check `SELECT * FROM pg_locks WHERE locktype='advisory'`."
            )
        # Cap the sleep at the deadline: a 5s poll must not overshoot a 900s
        # wait to 905s, and a sub-poll_s wait_s must still time out on time.
        time.sleep(min(poll_s, remaining))


def release(conn, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key(name),))


@contextlib.contextmanager
def advisory_lock(
    conn,
    name: str,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
):
    """Scoped form. Released on the way out, including on exception."""
    acquire_or_wait(conn, name, wait_s=wait_s, poll_s=poll_s)
    try:
        yield
    finally:
        release(conn, name)
