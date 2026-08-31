"""nflverse snap counts -> raw.nflverse_snap_counts (R27).

raw.nflverse_player_week carries no snap columns, so without this feed the
trend engine's headline rule has no input. nflverse publishes `offense_pct`
directly (a share, not a count over a denominator we compute), which is why
the R5 partial-publish short-denominator failure cannot reach snap_share.

The feed is keyed by `pfr_player_id`; every other table in this repo is
keyed by `gsis_id`. The resolution happens HERE, once, and a match rate
below MIN_MATCH_RATE is a hard failure — silently dropping unresolved
players would make a star's missing week look like a benching, which is
exactly the false FALLING signal the precision gate exists to prevent.
"""

import polars as pl
import psycopg2.extras

from ffi.ingest.base import BaseIngester, IngestError

REQUIRED_COLS = {
    "season",
    "week",
    "game_type",
    "player",
    "pfr_player_id",
    "position",
    "team",
    "offense_snaps",
    "offense_pct",
}
# The only positions this league's usage rules score. Offensive linemen are
# ~40% of the feed and carry no fantasy signal.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "FB")

# `position` in this feed is PFR's per-game free text, NOT nflverse's canonical
# position: it is truncated to four characters and carries team idiom. Matching
# SKILL_POSITIONS literally (as this task's brief drafted it) silently dropped
# Chase Brown's ENTIRE 2025 season — 17 weeks at 0.53-0.96 offense_pct, a
# textbook trigger for the headline "snap > 55% two consecutive weeks" rule —
# because Cincinnati codes its running backs "HB". That is the precise failure
# this module's match-rate floor exists to prevent, arriving through the
# position filter instead of the id join, so it is normalised here rather than
# left to whoever notices a starter missing from the trend report.
#
# Two normalisations, both verified against 2019-2025 REG (2026-08-31):
#   1. multi-role codes are truncated slashes ("RB/W" = RB/WR, "WR/R" = WR/RB,
#      "FB/D" = FB/DL) — take the PRIMARY token, the player's listed role.
#   2. HB/WB are PFR idiom for RB.
# Anything else keeps its code and is judged against SKILL_POSITIONS as-is.
POSITION_ALIASES = {"HB": "RB", "WB": "RB"}


def canonical_position() -> pl.Expr:
    """PFR position text -> the canonical code, aliased onto `position`."""
    return (
        pl.col("position")
        .str.to_uppercase()
        .str.split("/")
        .list.first()
        .replace(POSITION_ALIASES)
        .alias("position")
    )


DB_COLS = [
    "gsis_id",
    "season",
    "week",
    "team",
    "position",
    "offense_snaps",
    "offense_pct",
]


class NflverseSnapCountsIngester(BaseIngester):
    source = "nflverse_snap_counts"
    # Task 6's gate framework is opt-in per feed; no numeric threshold has been
    # fitted for snap counts yet, so this feed runs ungated (mode 'off') and
    # deliberately does NOT implement sanity_check().
    sanity_mode = "off"

    # 30 of 30 in fixtures; live 2025 resolution is ~0.97 for skill players.
    # A drop below this means the pfr_id -> gsis_id crosswalk changed shape,
    # not that players went missing.
    MIN_MATCH_RATE = 0.90

    def __init__(self, seasons: list[int]):
        self.seasons = seasons

    def fetch(self) -> dict:
        import nflreadpy

        return {
            "snaps": nflreadpy.load_snap_counts(seasons=self.seasons),
            "xwalk": nflreadpy.load_players()
            .select(["pfr_id", "gsis_id"])
            .drop_nulls(["pfr_id", "gsis_id"]),
        }

    @staticmethod
    def _skill_regular(snaps: pl.DataFrame) -> pl.DataFrame:
        """REG-season skill rows, with `position` REPLACED by its canonical
        code — so the value that reaches the table is the one every other
        table in this repo uses, not PFR's per-game idiom. Task 8 grouping by
        `position` must never see an 'HB' bucket beside the 'RB' one."""
        return snaps.with_columns(canonical_position()).filter(
            (pl.col("game_type") == "REG") & pl.col("position").is_in(SKILL_POSITIONS)
        )

    def _resolve(self, payload: dict) -> pl.DataFrame:
        rows = self._skill_regular(payload["snaps"])
        return rows.join(
            payload["xwalk"], left_on="pfr_player_id", right_on="pfr_id", how="inner"
        )

    def validate(self, payload) -> int:
        snaps = payload["snaps"]
        missing = REQUIRED_COLS - set(snaps.columns)
        if missing:
            raise IngestError(
                f"nflverse_snap_counts: expected columns missing: {sorted(missing)}. "
                f"Actual: {sorted(snaps.columns)}. Schema drift — investigate, "
                f"do not rename blindly."
            )
        eligible = self._skill_regular(snaps)
        if eligible.height == 0:
            raise IngestError(
                f"nflverse_snap_counts: zero rows for seasons {self.seasons} after "
                f"filtering to REG + {list(SKILL_POSITIONS)}"
            )
        matched = self._resolve(payload)
        rate = matched.height / eligible.height
        if rate < self.MIN_MATCH_RATE:
            raise IngestError(
                f"nflverse_snap_counts: pfr_id -> gsis_id match rate {rate:.3f} "
                f"({matched.height}/{eligible.height}) below floor "
                f"{self.MIN_MATCH_RATE} — refusing to load a feed with a hole in "
                f"it; unresolved players read downstream as benched (false FALLING)"
            )
        return matched.height

    def store(self, conn, run_id: int, payload) -> None:
        matched = self._resolve(payload)
        # Select by DB_COLS so the INSERT column list and the row tuple order
        # cannot drift apart (same-typed neighbours would swap silently).
        rows = matched.select(DB_COLS).rows()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM raw.nflverse_snap_counts WHERE season = ANY(%s)",
                (self.seasons,),
            )
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO raw.nflverse_snap_counts ({', '.join(DB_COLS)}) VALUES %s",
                rows,
                page_size=5000,
            )

    def _first_record(self, payload):
        return {c: None for c in payload["snaps"].columns}
