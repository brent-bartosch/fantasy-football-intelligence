import datetime
import json

import pytest

from ffi.ingest.base import IngestError
from ffi.ingest.sleeper_trending import SleeperTrendingIngester

# Shape verified live 2026-08-31:
# GET https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=200
# -> [{"count": 488052, "player_id": "8800"}, ...]
def _payload(n: int = 120) -> dict:
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
    assert ing.validate(_payload()) == 240


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
    today = datetime.datetime.now(datetime.timezone.utc).astimezone().date()
    assert {r[1] for r in rows} == {today}
    assert {r[2] for r in rows} == {24}
    assert json.loads(json.dumps(rows[0][3])) == payload["add"]
    assert run == ("sleeper_trending", "success", 240)
