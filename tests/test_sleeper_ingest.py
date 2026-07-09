import json
import pathlib
import pytest
from ffi.ingest.base import IngestError
from ffi.ingest.sleeper import SleeperProjectionsIngester

FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"
    ).read_text()
)


class FixtureIngester(SleeperProjectionsIngester):
    def fetch(self):
        return FIXTURE


def test_validate_passes_on_good_payload():
    ing = FixtureIngester(season=2025, week=5)
    assert ing.validate(FIXTURE) == 2


def test_validate_fails_when_first_downs_missing():
    broken = json.loads(json.dumps(FIXTURE))
    for rec in broken:
        rec["stats"].pop("pass_fd", None)
        rec["stats"].pop("rush_fd", None)
        rec["stats"].pop("rec_fd", None)
    ing = FixtureIngester(season=2025, week=5)
    with pytest.raises(IngestError, match="first-down"):
        ing.validate(broken)


def test_validate_fails_on_empty_payload():
    ing = FixtureIngester(season=2025, week=5)
    with pytest.raises(IngestError, match="empty"):
        ing.validate([])


def test_store_writes_snapshot(db):
    ing = FixtureIngester(season=2025, week=5)
    run_id = ing.run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT season, week, jsonb_array_length(payload) FROM raw.sleeper_projections WHERE run_id=%s",
            (run_id,),
        )
        assert cur.fetchone() == (2025, 5, 2)
