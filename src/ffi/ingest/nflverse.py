import polars as pl
import psycopg2.extras
from ffi.ingest.base import BaseIngester, IngestError

REQUIRED_COLS = {
    "player_id",
    "season",
    "week",
    "player_display_name",
    "position",
    "team",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_first_downs",
    "passing_interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_first_downs",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_first_downs",
    "punt_return_yards",
    "kickoff_return_yards",
    "rushing_fumbles_lost",
    "receiving_fumbles_lost",
    "sack_fumbles_lost",
}

_DB_COLS = [
    "gsis_id",
    "season",
    "week",
    "player_name",
    "position",
    "team",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_first_downs",
    "interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_first_downs",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_first_downs",
    "punt_return_yards",
    "kickoff_return_yards",
    "fumbles_lost",
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

    _STAT_COLS = [
        "completions",
        "attempts",
        "passing_yards",
        "passing_tds",
        "passing_first_downs",
        "passing_interceptions",
        "carries",
        "rushing_yards",
        "rushing_tds",
        "rushing_first_downs",
        "receptions",
        "targets",
        "receiving_yards",
        "receiving_tds",
        "receiving_first_downs",
        "punt_return_yards",
        "kickoff_return_yards",
        "rushing_fumbles_lost",
        "receiving_fumbles_lost",
        "sack_fumbles_lost",
    ]

    def store(self, conn, run_id: int, df: pl.DataFrame) -> None:
        # nflverse includes a few team-level artifact rows with null player_id.
        # Drop them only if provably empty (all stat columns zero/null);
        # otherwise fail loud — real stats without a player id means bad data.
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
        # fumbles_lost is a derived total across fumble types; nulls treated as 0
        # for the sum only (not a defaulted load-bearing field).
        df = df.with_columns(
            (
                pl.col("rushing_fumbles_lost").fill_null(0)
                + pl.col("receiving_fumbles_lost").fill_null(0)
                + pl.col("sack_fumbles_lost").fill_null(0)
            ).alias("fumbles_lost")
        )
        ordered_src = [
            "player_id",
            "season",
            "week",
            "player_display_name",
            "position",
            "team",
            "completions",
            "attempts",
            "passing_yards",
            "passing_tds",
            "passing_first_downs",
            "passing_interceptions",
            "carries",
            "rushing_yards",
            "rushing_tds",
            "rushing_first_downs",
            "receptions",
            "targets",
            "receiving_yards",
            "receiving_tds",
            "receiving_first_downs",
            "punt_return_yards",
            "kickoff_return_yards",
            "fumbles_lost",
        ]
        rows = df.select(ordered_src).rows()
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
