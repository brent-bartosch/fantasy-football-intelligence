import polars as pl
import pytest

from ffi.ingest.base import IngestError
from ffi.ingest.nflverse_snaps import NflverseSnapCountsIngester

# Column names verified against the live feed 2026-08-31.
SNAP_COLS = [
    "game_id",
    "season",
    "game_type",
    "week",
    "player",
    "pfr_player_id",
    "position",
    "team",
    "opponent",
    "offense_snaps",
    "offense_pct",
    "defense_snaps",
    "defense_pct",
    "st_snaps",
    "st_pct",
]


def _snaps(
    n: int = 30, *, game_type: str = "REG", position: str = "RB"
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["2025_01_A_B" for _ in range(n)],
            "season": [2025] * n,
            "game_type": [game_type] * n,
            "week": [1] * n,
            "player": [f"Player {i}" for i in range(n)],
            "pfr_player_id": [f"Pfr{i:04d}" for i in range(n)],
            "position": [position] * n,
            "team": ["SF"] * n,
            "opponent": ["SEA"] * n,
            "offense_snaps": [float(50 + i) for i in range(n)],
            "offense_pct": [0.5 + i / 200 for i in range(n)],
            "defense_snaps": [0.0] * n,
            "defense_pct": [0.0] * n,
            "st_snaps": [0.0] * n,
            "st_pct": [0.0] * n,
        },
        schema={
            "game_id": pl.String,
            "season": pl.Int64,
            "game_type": pl.String,
            "week": pl.Int64,
            "player": pl.String,
            "pfr_player_id": pl.String,
            "position": pl.String,
            "team": pl.String,
            "opponent": pl.String,
            "offense_snaps": pl.Float64,
            "offense_pct": pl.Float64,
            "defense_snaps": pl.Float64,
            "defense_pct": pl.Float64,
            "st_snaps": pl.Float64,
            "st_pct": pl.Float64,
        },
    )


def _xwalk(n: int = 30, matched: int | None = None) -> pl.DataFrame:
    matched = n if matched is None else matched
    return pl.DataFrame(
        {
            "pfr_id": [f"Pfr{i:04d}" for i in range(matched)],
            "gsis_id": [f"00-{i:07d}" for i in range(matched)],
        },
        schema={"pfr_id": pl.String, "gsis_id": pl.String},
    )


class FixtureIngester(NflverseSnapCountsIngester):
    def __init__(self, snaps, xwalk, **kw):
        super().__init__(**kw)
        self._payload = {"snaps": snaps, "xwalk": xwalk}

    def fetch(self):
        return self._payload


def test_validate_counts_matched_skill_rows():
    ing = FixtureIngester(_snaps(), _xwalk(), seasons=[2025])
    assert ing.validate(ing.fetch()) == 30


def test_validate_rejects_missing_columns():
    bad = _snaps().drop("offense_pct")
    ing = FixtureIngester(bad, _xwalk(), seasons=[2025])
    with pytest.raises(IngestError, match="offense_pct"):
        ing.validate(ing.fetch())


def test_validate_rejects_low_match_rate():
    ing = FixtureIngester(_snaps(30), _xwalk(30, matched=20), seasons=[2025])
    with pytest.raises(IngestError, match="match rate"):
        ing.validate(ing.fetch())


def test_validate_rejects_empty_frame():
    ing = FixtureIngester(_snaps(0), _xwalk(), seasons=[2025])
    with pytest.raises(IngestError, match="zero rows"):
        ing.validate(ing.fetch())


def test_non_skill_and_postseason_rows_are_excluded():
    # Offensive linemen and playoff games are not usage signal for this league.
    mixed = pl.concat(
        [_snaps(20), _snaps(20, position="T"), _snaps(20, game_type="POST")]
    )
    ing = FixtureIngester(mixed, _xwalk(20), seasons=[2025])
    assert ing.validate({"snaps": mixed, "xwalk": _xwalk(20)}) == 20


def test_pfr_position_idiom_is_not_dropped(db):
    """Regression: Cincinnati codes its RBs 'HB', and the literal
    SKILL_POSITIONS filter dropped Chase Brown's entire 2025 season (17 weeks
    at 0.53-0.96 offense_pct) — a starter reading as benched, which is the one
    failure this ingester exists to prevent. Multi-role codes are truncated
    slashes ('RB/W' = RB/WR) and resolve to their primary token.
    """
    idiom = pl.concat([_snaps(10, position="HB"), _snaps(10, position="RB/W")])
    ing = FixtureIngester(idiom, _xwalk(10), seasons=[2025])
    # 10 pfr ids, each appearing under both codes -> 20 eligible, 20 matched.
    assert ing.validate({"snaps": idiom, "xwalk": _xwalk(10)}) == 20
    # Stored under the canonical code, never as its own 'HB' bucket.
    ing_one = FixtureIngester(_snaps(10, position="HB"), _xwalk(10), seasons=[2025])
    ing_one.run(db)
    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT position FROM raw.nflverse_snap_counts")
        assert [r[0] for r in cur.fetchall()] == ["RB"]


def test_linemen_are_still_excluded_after_normalisation():
    # 'G/T' and 'OT' must not survive the slash split as skill positions.
    line = pl.concat([_snaps(10, position="G/T"), _snaps(10, position="OT")])
    ing = FixtureIngester(line, _xwalk(10), seasons=[2025])
    with pytest.raises(IngestError, match="zero rows"):
        ing.validate({"snaps": line, "xwalk": _xwalk(10)})


def test_store_writes_resolved_gsis_rows(db):
    ing = FixtureIngester(_snaps(30), _xwalk(30), seasons=[2025])
    run_id = ing.run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT gsis_id, season, week, team, position, offense_pct "
            "FROM raw.nflverse_snap_counts ORDER BY gsis_id LIMIT 1"
        )
        row = cur.fetchone()
        cur.execute("SELECT count(*) FROM raw.nflverse_snap_counts")
        total = cur.fetchone()[0]
        cur.execute("SELECT status FROM raw.ingest_runs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "success"
    assert total == 30
    assert row[0] == "00-0000000"
    assert row[1:5] == (2025, 1, "SF", "RB")
    assert row[5] == pytest.approx(0.5, abs=1e-4)


def test_rerun_replaces_the_season_rather_than_duplicating(db):
    FixtureIngester(_snaps(30), _xwalk(30), seasons=[2025]).run(db)
    FixtureIngester(_snaps(30), _xwalk(30), seasons=[2025]).run(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.nflverse_snap_counts")
        assert cur.fetchone()[0] == 30
