"""The ONLY module that sends a push (ARCHITECTURE §3a).

ntfy topic from `NTFY_TOPIC_URL` (a capability URL — never logged or rendered
into an artifact). A hard 3-per-week cap is enforced in code against
`push_log`, not by convention: over-cap pushes are downgraded to the next
report and RECORDED as suppressed, so a suppressed push is as visible as a
sent one (ADR D8).

Internal imports: `ffi.db` and `ffi.health` only, so this stays importable from
any job.
"""
from __future__ import annotations

import datetime
import os

import requests
from dotenv import load_dotenv

from ffi import db

load_dotenv()

EVENT_CLASSES = frozenset({"claim_deadline", "inactive_contingency", "qb_watchlist"})
WEEKLY_CAP = 3


class PushResult:
    def __init__(self, status: str, reason: str):
        self.status = status
        self.reason = reason

    def __repr__(self) -> str:
        return f"PushResult(status={self.status!r}, reason={self.reason!r})"


def _ntfy_url() -> str:
    url = os.getenv("NTFY_TOPIC_URL")
    if not url:
        raise RuntimeError(
            "NTFY_TOPIC_URL is not set — refusing to guess a push target (fail-loud)"
        )
    return url


def _send(title: str, body: str, url: str) -> None:
    resp = requests.post(
        url,
        data=(body or title).encode("utf-8"),
        headers={"Title": title},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"ntfy push failed with HTTP {resp.status_code}: {resp.text[:200]}"
        )


def _sent_this_week(conn, now: datetime.datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.push_log WHERE status='sent' "
            "AND sent_at >= date_trunc('week', %s::timestamptz) "
            "AND sent_at < date_trunc('week', %s::timestamptz) + interval '7 days'",
            (now, now),
        )
        return int(cur.fetchone()[0])


def _record(conn, event_class, title, body, status, now, week) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.push_log (event_class, title, body, status, week, sent_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (event_class, title, body, status, week, now),
        )
    conn.commit()


def push(
    event_class: str,
    title: str,
    body: str,
    conn=None,
    now: datetime.datetime | None = None,
    send=None,
) -> PushResult:
    """Send a push, subject to the 3-per-week cap. Over-cap -> suppressed.

    `send` is injectable for tests (defaults to the real ntfy transport).
    """
    if event_class not in EVENT_CLASSES:
        raise ValueError(
            f"push: unknown event_class {event_class!r} (known: {sorted(EVENT_CLASSES)})"
        )
    now = now or datetime.datetime.now(datetime.timezone.utc)
    close = conn is None
    conn = conn or db.connect()
    try:
        sent = _sent_this_week(conn, now)
        week = now.isocalendar()[1]
        if sent >= WEEKLY_CAP:
            _record(conn, event_class, title, body, "suppressed", now, week)
            return PushResult(
                "suppressed",
                f"weekly cap {WEEKLY_CAP} reached ({sent} already sent); downgraded to report",
            )
        if send is not None:
            send(title, body, None)
        else:
            _send(title, body, _ntfy_url())
        _record(conn, event_class, title, body, "sent", now, week)
        return PushResult("sent", f"pushed to ntfy topic ({sent + 1}/{WEEKLY_CAP} this week)")
    finally:
        if close:
            conn.close()
