import polars as pl
import pytest
from ffi.ingest.base import IngestError
from ffi.ingest.nflverse import (
    COLUMN_MAP,
    DERIVED_SUMS,
    NflversePlayerWeekIngester,
    REQUIRED_COLS,
)


def _frame(cols):
    return pl.DataFrame({c: [None] for c in cols})


def test_validate_passes_with_required_columns():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _frame(REQUIRED_COLS)
    assert ing.validate(df) == 1


def test_validate_fails_on_missing_first_down_column():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _frame(REQUIRED_COLS - {"rushing_first_downs"})
    with pytest.raises(IngestError, match="rushing_first_downs"):
        ing.validate(df)


_STR_COLS = {"player_id", "player_display_name", "position", "team"}


def _row_frame(rows):
    """Build a REQUIRED_COLS frame from per-row override dicts.

    String columns default to "X", stat columns to 0; pass None explicitly
    to get a null value.
    """
    data = {c: [] for c in REQUIRED_COLS}
    for r in rows:
        for c in REQUIRED_COLS:
            data[c].append(r.get(c, "X" if c in _STR_COLS else 0))
    schema = {c: (pl.Utf8 if c in _STR_COLS else pl.Int64) for c in REQUIRED_COLS}
    return pl.DataFrame(data, schema_overrides=schema)


def test_derive_rows_sums_fumbles_lost_treating_nulls_as_zero():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _row_frame(
        [
            {
                "player_id": "00-0000001",
                "rushing_fumbles_lost": 1,
                "receiving_fumbles_lost": None,
                "sack_fumbles_lost": 2,
            }
        ]
    )
    rows = ing._derive_rows(df)
    assert len(rows) == 1
    db_cols = [db for _, db in COLUMN_MAP] + list(DERIVED_SUMS)
    row = dict(zip(db_cols, rows[0]))
    assert row["fumbles_lost"] == 3


def test_derive_rows_drops_null_player_id_row_with_all_empty_stats():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _row_frame(
        [
            {"player_id": "00-0000001", "rushing_yards": 10},
            {"player_id": None, "receptions": None},  # artifact row: stats all 0/None
        ]
    )
    rows = ing._derive_rows(df)
    assert len(rows) == 1
    assert rows[0][0] == "00-0000001"


def test_derive_rows_raises_on_null_player_id_row_with_nonzero_stats():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _row_frame([{"player_id": None, "rushing_yards": 5}])
    with pytest.raises(IngestError, match="1 rows"):
        ing._derive_rows(df)


def test_derive_rows_sums_fumbles_and_two_point_conversions_and_maps_special_teams_tds():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _row_frame(
        [
            {
                "player_id": "00-0000001",
                "rushing_fumbles": 1,
                "receiving_fumbles": None,
                "sack_fumbles": 1,
                "passing_2pt_conversions": 1,
                "rushing_2pt_conversions": None,
                "receiving_2pt_conversions": 1,
                "special_teams_tds": 2,
            }
        ]
    )
    rows = ing._derive_rows(df)
    assert len(rows) == 1
    db_cols = [db for _, db in COLUMN_MAP] + list(DERIVED_SUMS)
    row = dict(zip(db_cols, rows[0]))
    assert row["special_teams_tds"] == 2
    assert row["fumbles"] == 2
    assert row["two_point_conversions"] == 2
