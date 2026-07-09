import polars as pl
import pytest
from ffi.ingest.base import IngestError
from ffi.ingest.nflverse import NflversePlayerWeekIngester, REQUIRED_COLS


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
