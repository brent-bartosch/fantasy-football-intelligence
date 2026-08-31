import datetime

import pytest
import requests

from ffi.ingest import sleeper_trending as st
from ffi.ingest.base import IngestError
from ffi.ingest.sleeper_trending import LEAGUE_TZ, SleeperTrendingIngester

# Shape verified live 2026-08-31:
# GET https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=100
# -> [{"count": 488052, "player_id": "8800"}, ...]
# The server caps each direction at 100 rows regardless of `limit`.
def _payload(n: int = 100) -> dict:
    return {
        "add": [{"player_id": str(1000 + i), "count": 10_000 - i} for i in range(n)],
        "drop": [{"player_id": str(5000 + i), "count": 9_000 - i} for i in range(n)],
    }


class FixtureIngester(SleeperTrendingIngester):
    def __init__(self, payload, **kw):
        super().__init__(**kw)
        self._payload = payload

    def fetch(self):
        return self._payload


def test_validate_counts_both_directions():
    ing = FixtureIngester(_payload())
    assert ing.validate(_payload()) == 200


@pytest.mark.parametrize("bad", [[], "add", None, 7])
def test_validate_rejects_non_dict_payload(bad):
    with pytest.raises(IngestError, match="expected a dict keyed by trend type"):
        FixtureIngester(bad).validate(bad)


def test_validate_rejects_missing_direction():
    bad = _payload()
    del bad["drop"]
    with pytest.raises(IngestError, match="missing 'drop'"):
        FixtureIngester(bad).validate(bad)


def test_validate_rejects_thin_payload():
    thin = {"add": _payload(5)["add"], "drop": _payload(5)["drop"]}
    with pytest.raises(IngestError, match="only 5 rows"):
        FixtureIngester(thin).validate(thin)


def test_validate_rejects_record_shape_drift():
    drifted = _payload()
    drifted["add"][0] = {"playerId": "1000", "count": 1}
    with pytest.raises(IngestError, match="schema drift"):
        FixtureIngester(drifted).validate(drifted)


def test_run_writes_two_dated_snapshots_and_a_success_run(db):
    payload = _payload()
    run_id = FixtureIngester(payload).run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT trend_type, archive_date, lookback_hours, payload "
            "FROM raw.sleeper_trending WHERE run_id=%s ORDER BY trend_type",
            (run_id,),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT source, status, row_count FROM raw.ingest_runs WHERE run_id=%s",
            (run_id,),
        )
        run = cur.fetchone()
    assert [r[0] for r in rows] == ["add", "drop"]
    # The archive day is the league day (America/Los_Angeles), not the day in
    # whatever timezone the test host happens to be set to.
    today = datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ).date()
    assert {r[1] for r in rows} == {today}
    assert {r[2] for r in rows} == {24}
    assert rows[0][3] == payload["add"]
    assert run == ("sleeper_trending", "success", 200)


def test_thin_day_writes_no_rows_and_records_a_failed_run(db):
    """The safety property: a degraded day must leave the archive untouched.

    A partial write here is unrecoverable (R8) — a half-archived day looks
    like a real day forever — so validate must fail *before* store runs.
    """
    thin = {"add": _payload(5)["add"], "drop": _payload(5)["drop"]}
    with pytest.raises(IngestError, match="only 5 rows"):
        FixtureIngester(thin).run(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.sleeper_trending")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT status, row_count, error FROM raw.ingest_runs "
            "WHERE source='sleeper_trending'"
        )
        runs = cur.fetchall()
    assert [r[0] for r in runs] == ["failed"]
    assert runs[0][1] is None
    assert "only 5 rows" in runs[0][2]


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _mock_transport(monkeypatch, responses):
    """Script `requests.get` with a queue of responses/exceptions; capture
    the calls made and every sleep the retry loop asks for."""
    calls, sleeps = [], []
    queue = list(responses)

    def fake_get(url, **kw):
        calls.append(url)
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(st.requests, "get", fake_get)
    monkeypatch.setattr(st.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_fetch_retries_a_500_then_succeeds(monkeypatch):
    p = _payload()
    calls, sleeps = _mock_transport(
        monkeypatch,
        [_Resp(500, text="upstream"), _Resp(200, p["add"]), _Resp(200, p["drop"])],
    )
    assert SleeperTrendingIngester().fetch() == p
    assert len(calls) == 3  # add retried once, drop clean
    assert calls[0].endswith("/add") and calls[1].endswith("/add")
    assert calls[2].endswith("/drop")
    assert sleeps == [2]  # first backoff only


def test_fetch_retries_transport_errors_then_gives_up(monkeypatch):
    calls, sleeps = _mock_transport(
        monkeypatch, [requests.ConnectionError("boom") for _ in range(3)]
    )
    with pytest.raises(IngestError, match="'add' failed after 3 attempts"):
        SleeperTrendingIngester().fetch()
    assert len(calls) == 3
    assert sleeps == [2, 4]  # full backoff ladder, then fail loud


def test_fetch_does_not_retry_a_4xx(monkeypatch):
    calls, sleeps = _mock_transport(monkeypatch, [_Resp(404, text="gone")])
    with pytest.raises(IngestError, match="HTTP 404"):
        SleeperTrendingIngester().fetch()
    assert len(calls) == 1
    assert sleeps == []


def test_gate_failure_stores_the_payload_and_flags_the_run(db):
    """Observe-and-log: the archive must land even on a suspect day (R8),
    but the run must never look successful."""
    first = _payload()
    FixtureIngester(first).run(db)
    # Force an earlier archive_date so the gate has a prior to compare to.
    with db.cursor() as cur:
        cur.execute("UPDATE raw.sleeper_trending SET archive_date = archive_date - 1")
    db.commit()

    reversed_counts = {
        "add": [{"player_id": str(1000 + i), "count": i + 1} for i in range(120)],
        "drop": first["drop"],
    }
    run_id = FixtureIngester(reversed_counts).run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,)
        )
        status, error = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM raw.sleeper_trending WHERE run_id=%s", (run_id,)
        )
        stored = cur.fetchone()[0]
    assert status == "sanity_warned"
    assert stored == 2  # the archive still landed
    # The row has to say WHY, or the operator reading tomorrow's briefing has
    # nothing to act on: this reversal trips the rank-correlation gate.
    assert "rank correlation" in error


def _backdate(db):
    """Age the stored archive a day so the next run has a prior to compare to."""
    with db.cursor() as cur:
        cur.execute("UPDATE raw.sleeper_trending SET archive_date = archive_date - 1")
    db.commit()


def test_fieldset_gate_catches_drift_on_a_record_other_than_the_first(db):
    """Union-based field-set: a key added to record 40 and not record 0 must
    still fire. Comparing `payload[0]` only would sail straight past it."""
    first = _payload()
    FixtureIngester(first).run(db)
    _backdate(db)

    drifted = _payload()
    drifted["add"][40]["trend_score"] = 0.5  # record 0 is untouched
    run_id = FixtureIngester(drifted).run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,)
        )
        status, error = cur.fetchone()
    assert status == "sanity_warned"
    assert "added=['trend_score']" in error
    # Observe-and-log still archives the day (R8).
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw.sleeper_trending WHERE run_id=%s", (run_id,)
        )
        assert cur.fetchone()[0] == 2


def test_identical_field_union_passes_the_gate(db):
    FixtureIngester(_payload()).run(db)
    _backdate(db)
    run_id = FixtureIngester(_payload()).run(db)
    with db.cursor() as cur:
        cur.execute("SELECT status FROM raw.ingest_runs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "success"


def _run_row(db, run_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error, sanity_rho FROM raw.ingest_runs WHERE run_id=%s",
            (run_id,),
        )
        return cur.fetchone()


def _age_archive(db, days: int):
    """Backdate the whole stored archive by `days` (both directions)."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE raw.sleeper_trending "
            "SET archive_date = archive_date - make_interval(days => %s)",
            (days,),
        )
    db.commit()


def test_passing_day_records_the_measured_rho(db):
    """Same soak instrumentation as projections: rho on the days that pass."""
    FixtureIngester(_payload()).run(db)
    _backdate(db)
    run_id = FixtureIngester(_payload()).run(db)
    status, error, rho = _run_row(db, run_id)
    assert (status, error) == ("success", None)
    assert rho == pytest.approx(1.0)


def test_warned_day_is_not_used_as_the_next_days_baseline(db):
    """R8 forces this feed to archive suspect days — so they sit in the table
    looking exactly like clean ones. If a warned day could be a baseline, the
    gate would re-normalize onto the drift it flagged and the SECOND day of a
    real break would report clean."""
    clean = FixtureIngester(_payload()).run(db)
    _backdate(db)  # the clean day is now "yesterday"

    drifted = _payload()
    drifted["add"][40]["trend_score"] = 0.5
    warned = FixtureIngester(drifted).run(db)
    assert _run_row(db, warned)[0] == "sanity_warned"

    # Move both stored days back one so "today" is free again: yesterday is
    # now the WARNED day, the day before is the clean one.
    _backdate(db)
    baseline = SleeperTrendingIngester()._prior_add_rows(db)
    assert baseline.run_id == clean

    # End-to-end: today's payload carries the same drifted key as yesterday's
    # warned archive. Against the clean baseline it must still fire.
    run_id = FixtureIngester(drifted).run(db)
    status, error, _ = _run_row(db, run_id)
    assert status == "sanity_warned"
    assert "added=['trend_score']" in error


def test_gate_message_names_the_baseline_archive_date(db):
    baseline_run = FixtureIngester(_payload()).run(db)
    _backdate(db)
    baseline_day = datetime.datetime.now(datetime.timezone.utc).astimezone(
        LEAGUE_TZ
    ).date() - datetime.timedelta(days=1)

    drifted = _payload()
    drifted["add"][7]["trend_score"] = 0.5
    run_id = FixtureIngester(drifted).run(db)
    status, error, _ = _run_row(db, run_id)
    assert status == "sanity_warned"
    assert f"baseline run {baseline_run}" in error
    assert baseline_day.isoformat() in error
    assert "STALE BASELINE" not in error


def test_stale_baseline_note_appears_but_never_fails_the_run(db):
    """A 10-day-old baseline means the archive has a gap — say so on the
    message, but the staleness itself must not warn or fail anything."""
    FixtureIngester(_payload()).run(db)
    _age_archive(db, 10)

    drifted = _payload()
    drifted["add"][7]["trend_score"] = 0.5
    run_id = FixtureIngester(drifted).run(db)
    status, error, _ = _run_row(db, run_id)
    assert status == "sanity_warned"
    assert "STALE BASELINE: 10d old" in error
    assert "added=['trend_score']" in error

    # A stale baseline with a clean payload is still a clean run.
    clean = FixtureIngester(_payload()).run(db)
    assert _run_row(db, clean)[0] == "success"


def test_non_numeric_count_fails_as_a_named_gate_error(db):
    """A count that arrives as a string is type drift, and must surface as a
    gate error naming the record — not a bare ValueError from a comprehension."""
    bad = _payload()
    bad["add"][3]["count"] = "many"
    run_id = FixtureIngester(bad).run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,)
        )
        status, error = cur.fetchone()
    assert status == "sanity_warned"
    assert "non-numeric" in error and "player_id='1003'" in error
