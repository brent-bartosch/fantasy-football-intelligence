"""scripts/notify.py: 3/week cap + fail-loud transport."""
import datetime

import pytest

import notify


def _now():
    return datetime.datetime(2026, 9, 9, 12, 0, tzinfo=datetime.timezone.utc)


def test_push_sends_and_records(db):
    sent = []
    r = notify.push(
        "claim_deadline",
        "title",
        "body",
        conn=db,
        now=_now(),
        send=lambda t, b, u: sent.append((t, b)),
    )
    assert r.status == "sent"
    assert len(sent) == 1
    with db.cursor() as cur:
        cur.execute("SELECT status FROM public.push_log")
        assert [row[0] for row in cur.fetchall()] == ["sent"]


def test_push_caps_at_three_per_week(db):
    sent = []
    now = _now()
    for _ in range(3):
        notify.push("claim_deadline", "t", "b", conn=db, now=now, send=lambda *a: sent.append(a))
    assert len(sent) == 3
    r = notify.push(
        "claim_deadline", "t", "b", conn=db, now=now, send=lambda *a: sent.append(a)
    )
    assert r.status == "suppressed"
    assert len(sent) == 3  # no fourth send


def test_push_rejects_unknown_event_class(db):
    with pytest.raises(ValueError, match="event_class"):
        notify.push("bogus", "t", "b", conn=db, now=_now(), send=lambda *a: None)
