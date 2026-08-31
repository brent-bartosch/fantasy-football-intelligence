import json
import pathlib
import pytest
from ffi.ingest.base import IngestError
from ffi.ingest.gates import SanityGateError
from ffi.ingest.sleeper import SleeperProjectionsIngester

FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"
    ).read_text()
)


class FixtureIngester(SleeperProjectionsIngester):
    # The live default floors (MIN_PROJECTED) assume a full-season payload
    # (hundreds of records/position). FIXTURE has 2 records total, so the
    # existing ratio/FD tests below need the population-collapse floor
    # disabled to isolate what they're actually testing.
    MIN_PROJECTED = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}

    # Same reasoning for the sanity gate's coverage floor: MIN_RANKED=400 is
    # sized for the live payload (628 positive pts_ppr, probed 2026-08-31).
    # The gate itself is exercised deliberately below rather than incidentally
    # tripping every store test.
    MIN_RANKED = 0

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


def test_validate_fails_when_meaningfully_projected_population_collapses():
    # R5: the ratio guard's denominator is "meaningfully projected" records —
    # if only a handful of records carry real stats (rest are ADP-only
    # metadata), the ratio can still read 100% and pass even though the
    # population has collapsed. Uses the LIVE default MIN_PROJECTED (QB: 60)
    # to exercise the real floor, not a test override.
    payload = []
    for i in range(10):
        payload.append(
            {
                "player_id": f"real-{i}",
                "player": {"position": "QB"},
                "stats": {"pass_cmp": 20.0, "pass_yd": 250.0},
            }
        )
    for i in range(490):
        payload.append(
            {
                "player_id": f"adp-{i}",
                "player": {"position": "QB"},
                "stats": {"adp_std": 150.0 + i, "gp": 16},
            }
        )
    ing = SleeperProjectionsIngester(season=2025, week=None)
    with pytest.raises(IngestError, match="collapsed"):
        ing.validate(payload)


def test_validate_fails_when_position_has_zero_meaningfully_projected_records():
    # total==0: every QB record is ADP-only metadata, so the ratio guard's
    # `if total` short-circuit skips it entirely — the floor must still trip.
    payload = [
        {
            "player_id": f"adp-{i}",
            "player": {"position": "QB"},
            "stats": {"adp_std": 100.0 + i, "gp": 16},
        }
        for i in range(5)
    ]
    ing = SleeperProjectionsIngester(season=2025, week=None)
    with pytest.raises(IngestError, match="collapsed"):
        ing.validate(payload)


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


def test_gate_hard_fails_and_stores_nothing_when_pts_ppr_coverage_collapses(db):
    """The hard-fail MECHANISM, driven by an explicit mode override.

    The class default is soaking in 'warn' until 2026-09-08 (see
    sleeper.py), so this test does not read the config — it asserts that
    asking for 'fail' produces the end-state behaviour: nothing reaches
    raw.sleeper_projections and the run is recorded 'sanity_failed'. When the
    soak ends the flip is a one-literal change and this test still holds.
    """

    class LiveFloorIngester(FixtureIngester):
        MIN_RANKED = SleeperProjectionsIngester.MIN_RANKED  # live floor: 400

    ing = LiveFloorIngester(season=2025, week=5, sanity_mode="fail")
    assert ing.sanity_mode == "fail"
    with pytest.raises(SanityGateError, match="positive 'pts_ppr'"):
        ing.run(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.sleeper_projections")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE source='sleeper_projections'"
        )
        status, error = cur.fetchone()
    assert status == "sanity_failed"
    assert "pts_ppr" in error


def test_projections_soak_in_warn_mode_stores_but_flags_the_run(db):
    """The soak setting itself: same collapsed payload, default mode."""

    class LiveFloorIngester(FixtureIngester):
        MIN_RANKED = SleeperProjectionsIngester.MIN_RANKED

    run_id = LiveFloorIngester(season=2025, week=5).run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,)
        )
        status, error = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM raw.sleeper_projections WHERE run_id=%s", (run_id,)
        )
        stored = cur.fetchone()[0]
    assert status == "sanity_warned"
    assert stored == 1
    assert "pts_ppr" in error


def _seed_snapshot(db, season, week, payload):
    """Insert a stored snapshot the way a real successful run leaves one."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.ingest_runs (source, status) "
            "VALUES ('sleeper_projections','success') RETURNING run_id"
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO raw.sleeper_projections (run_id, season, week, payload) "
            "VALUES (%s,%s,%s,%s)",
            (run_id, season, week, json.dumps(payload)),
        )
    db.commit()
    return run_id


def test_prior_payload_ignores_the_season_level_snapshot_for_a_weekly_run(db):
    """A week-5 run must never correlate against the week-NULL snapshot.

    Season totals and one week's projection are different populations on
    different scales; the old hard-coded `WHERE week IS NULL` handed the
    season payload to every weekly run's rank gate.
    """
    season_level = json.loads(json.dumps(FIXTURE))
    for rec in season_level:
        rec["stats"]["pts_ppr"] = 300.0  # season-scale magnitudes
    _seed_snapshot(db, 2025, None, season_level)

    ing = FixtureIngester(season=2025, week=5)
    assert ing._prior_payload(db) is None

    # ...and once a week-5 snapshot exists, that is what it finds.
    week_five = json.loads(json.dumps(FIXTURE))
    for rec in week_five:
        rec["stats"]["pts_ppr"] = 18.0
    _seed_snapshot(db, 2025, 5, week_five)
    prior = ing._prior_payload(db)
    assert prior is not None
    assert {rec["stats"]["pts_ppr"] for rec in prior} == {18.0}

    # A season-level run still finds its own (week IS NULL) baseline: the
    # parameterised `IS NOT DISTINCT FROM` has to match NULL, not drop it.
    season_prior = FixtureIngester(season=2025, week=None)._prior_payload(db)
    assert {rec["stats"]["pts_ppr"] for rec in season_prior} == {300.0}


def test_prior_payload_ignores_another_season(db):
    _seed_snapshot(db, 2024, 5, FIXTURE)
    assert FixtureIngester(season=2025, week=5)._prior_payload(db) is None


def _synthetic(n: int = 30, tail_extra: dict | None = None) -> list:
    """n QB records with a stable pts_ppr ordering (so the rank gate passes),
    optionally with extra stat keys on the LAST record only."""
    payload = []
    for i in range(n):
        stats = {"pass_cmp": 20.0 + i, "pass_yd": 200.0 + i, "pts_ppr": float(n - i)}
        if tail_extra and i == n - 1:
            stats.update(tail_extra)
        payload.append(
            {"player_id": f"p{i}", "player": {"position": "QB"}, "stats": stats}
        )
    return payload


class PayloadIngester(FixtureIngester):
    def __init__(self, payload, **kw):
        super().__init__(**kw)
        self._payload = payload

    def fetch(self):
        return self._payload


def _status_and_error(db, run_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,)
        )
        return cur.fetchone()


def test_projections_fieldset_gate_catches_a_key_that_vanishes_from_one_record(db):
    """The adp_2qb failure mode, union-based.

    `adp_2qb` lives on the LAST record only in the prior snapshot, so a
    field-set built from record[0] would never see it disappear. The union
    does. Ordering is unchanged between the two payloads, so the only thing
    that can fire here is the field-set check.
    """
    _seed_snapshot(db, 2025, 5, _synthetic(tail_extra={"adp_2qb": 42.0}))
    run_id = PayloadIngester(_synthetic(), season=2025, week=5).run(db)
    status, error = _status_and_error(db, run_id)
    assert status == "sanity_warned"
    assert "removed=['adp_2qb']" in error


def test_projections_fieldset_gate_catches_a_key_added_to_one_record(db):
    _seed_snapshot(db, 2025, 5, _synthetic())
    run_id = PayloadIngester(
        _synthetic(tail_extra={"pass_2pt": 1.0}), season=2025, week=5
    ).run(db)
    status, error = _status_and_error(db, run_id)
    assert status == "sanity_warned"
    assert "added=['pass_2pt']" in error


def test_projections_gates_pass_on_an_identical_snapshot(db):
    _seed_snapshot(db, 2025, 5, _synthetic())
    run_id = PayloadIngester(_synthetic(), season=2025, week=5).run(db)
    assert _status_and_error(db, run_id) == ("success", None)
