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
