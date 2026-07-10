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


def test_validate_warns_but_passes_when_first_downs_missing():
    # FD (*_fd) is diagnostic only (post-R16 amendment): native FD is not
    # consumed by scoring, so missing FD coverage should warn, not block.
    broken = json.loads(json.dumps(FIXTURE))
    for rec in broken:
        rec["stats"].pop("pass_fd", None)
        rec["stats"].pop("rush_fd", None)
        rec["stats"].pop("rec_fd", None)
    ing = FixtureIngester(season=2025, week=5)
    assert ing.validate(broken) == 2


def test_validate_fails_when_all_qbs_lack_pass_cmp():
    # pass_cmp (completions) is QB's load-bearing volume key: it's directly
    # scored (pass_completions weight) and feeds FD imputation. pass_att is
    # deliberately NOT guarded — it's unscored/unused (see class docstring).
    payload = [
        {
            "player_id": "1",
            "player": {"position": "QB"},
            "stats": {"pass_yd": 4000.0, "pass_td": 25.0},
        },
        {
            "player_id": "2",
            "player": {"position": "QB"},
            "stats": {"pass_yd": 3800.0, "pass_td": 20.0},
        },
    ]
    ing = FixtureIngester(season=2025, week=None)
    with pytest.raises(IngestError, match="pass_cmp"):
        ing.validate(payload)


def test_validate_passes_when_records_lack_player_position():
    # No 'player' key, or a 'player' dict without 'position' — not counted
    # toward any position's FD ratio, so their missing FD fields can't trip
    # the per-position guard (they're simply excluded from the denominator).
    payload = [
        {"player_id": "1", "stats": {"pts_ppr": 5.0}},
        {"player_id": "2", "player": {}, "stats": {"pts_ppr": 3.0}},
        {"player_id": "3", "player": {"position": "LS"}, "stats": {"pts_ppr": 0.0}},
    ]
    ing = FixtureIngester(season=2025, week=None)
    assert ing.validate(payload) == 3


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
