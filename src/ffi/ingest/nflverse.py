import polars as pl
import psycopg2.extras
from ffi.ingest.base import BaseIngester, IngestError

# ONE source→DB mapping. REQUIRED_COLS, insert order, and stat columns all
# derive from this — extend HERE (and the migration) when adding columns.
COLUMN_MAP: list[tuple[str, str]] = [
    ("player_id", "gsis_id"),
    ("season", "season"),
    ("week", "week"),
    ("player_display_name", "player_name"),
    ("position", "position"),
    ("team", "team"),
    ("completions", "completions"),
    ("attempts", "attempts"),
    ("passing_yards", "passing_yards"),
    ("passing_tds", "passing_tds"),
    ("passing_first_downs", "passing_first_downs"),
    ("passing_interceptions", "interceptions"),
    ("carries", "carries"),
    ("rushing_yards", "rushing_yards"),
    ("rushing_tds", "rushing_tds"),
    ("rushing_first_downs", "rushing_first_downs"),
    ("receptions", "receptions"),
    ("targets", "targets"),
    ("receiving_yards", "receiving_yards"),
    ("receiving_tds", "receiving_tds"),
    ("receiving_first_downs", "receiving_first_downs"),
    ("punt_return_yards", "punt_return_yards"),
    ("kickoff_return_yards", "kickoff_return_yards"),
    ("special_teams_tds", "special_teams_tds"),
    ("fg_made_0_19", "fg_made_0_19"),
    ("fg_made_20_29", "fg_made_20_29"),
    ("fg_made_30_39", "fg_made_30_39"),
    ("fg_made_40_49", "fg_made_40_49"),
    ("fg_missed_0_19", "fg_missed_0_19"),
    ("fg_missed_20_29", "fg_missed_20_29"),
    ("fg_missed_30_39", "fg_missed_30_39"),
    ("pat_made", "pat_made"),
    ("pat_missed", "pat_missed"),
]
# db_col -> source columns summed (fill_null(0) inside the sum only).
DERIVED_SUMS: dict[str, list[str]] = {
    "fumbles_lost": [
        "rushing_fumbles_lost",
        "receiving_fumbles_lost",
        "sack_fumbles_lost",
    ],
    "fumbles": ["rushing_fumbles", "receiving_fumbles", "sack_fumbles"],
    "two_point_conversions": [
        "passing_2pt_conversions",
        "rushing_2pt_conversions",
        "receiving_2pt_conversions",
    ],
    # league bins 50+ together; nflverse splits 50_59 / 60_.
    "fg_made_50_plus": ["fg_made_50_59", "fg_made_60_"],
}

_IDENTITY_SRC = {
    "player_id",
    "season",
    "week",
    "player_display_name",
    "position",
    "team",
}
REQUIRED_COLS = {src for src, _ in COLUMN_MAP} | {
    c for cols in DERIVED_SUMS.values() for c in cols
}
_DB_COLS = [db for _, db in COLUMN_MAP] + list(DERIVED_SUMS)
_STAT_COLS = [src for src, _ in COLUMN_MAP if src not in _IDENTITY_SRC] + [
    c for cols in DERIVED_SUMS.values() for c in cols
]


class NflversePlayerWeekIngester(BaseIngester):
    source = "nflverse_player_week"

    def __init__(self, seasons: list[int]):
        self.seasons = seasons

    def fetch(self):
        import nflreadpy

        return nflreadpy.load_player_stats(seasons=self.seasons)

    def validate(self, df: pl.DataFrame) -> int:
        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            raise IngestError(
                f"nflverse: expected columns missing: {sorted(missing)}. "
                f"Actual columns: {sorted(df.columns)[:40]}... Schema drift — investigate, do not rename blindly."
            )
        if df.height == 0:
            raise IngestError(f"nflverse: zero rows for seasons {self.seasons}")
        return df.height

    # Class attribute alias to the module-level derivation (kept for internal
    # references); see COLUMN_MAP/DERIVED_SUMS above for the single source of
    # truth.
    _STAT_COLS = _STAT_COLS

    def _derive_rows(self, df: pl.DataFrame) -> list[tuple]:
        """Pure row derivation: null-id guard, derived sums, insert order."""
        null_id = df.filter(pl.col("player_id").is_null())
        if null_id.height:
            nonzero = null_id.filter(
                pl.sum_horizontal(pl.col(c).fill_null(0).abs() for c in self._STAT_COLS)
                > 0
            )
            if nonzero.height:
                raise IngestError(
                    f"nflverse: {nonzero.height} rows have null player_id but "
                    f"nonzero stats — refusing to drop or load them."
                )
            df = df.filter(pl.col("player_id").is_not_null())
        for db_col, src_cols in DERIVED_SUMS.items():
            df = df.with_columns(
                sum((pl.col(c).fill_null(0) for c in src_cols), pl.lit(0)).alias(db_col)
            )
        ordered_src = [src for src, _ in COLUMN_MAP] + list(DERIVED_SUMS)
        return df.select(ordered_src).rows()

    def store(self, conn, run_id: int, df: pl.DataFrame) -> None:
        rows = self._derive_rows(df)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM raw.nflverse_player_week WHERE season = ANY(%s)",
                (self.seasons,),
            )
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO raw.nflverse_player_week ({', '.join(_DB_COLS)}) VALUES %s",
                rows,
                page_size=5000,
            )

    def _first_record(self, payload):
        return (
            {c: None for c in payload.columns}
            if isinstance(payload, pl.DataFrame)
            else None
        )
