"""raw.nflverse_player_week row (dict) -> StatLine.

KNOWN_GAPS: league-scored stats nflverse does not carry. They stay None in the
StatLine (None = source lacks the stat) and every consumer of nflverse-scored
points inherits the documented bias below — see the divergence audit."""
from ffi.ingest.base import IngestError
from ffi.scoring.statline import StatLine

KNOWN_GAPS = {
    "pick_sixes": "not in nflverse player stats; league -4 each; rare (~1 QB-week in ~60)",
    "offensive_fumble_return_tds": "not in nflverse; league +6; very rare",
    "return_tds": "approximated by special_teams_tds (includes all ST TDs)",
}

_REQUIRED = (
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_first_downs",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "receiving_first_downs",
    "passing_first_downs",
    "punt_return_yards",
    "kickoff_return_yards",
    "fumbles",
    "fumbles_lost",
    "two_point_conversions",
    "special_teams_tds",
)


def stat_line_from_nflverse(row: dict) -> StatLine:
    missing = [k for k in _REQUIRED if k not in row]
    if missing:
        raise IngestError(
            f"nflverse row missing columns {missing} — re-ingest after Task 6 Step 3?"
        )

    def n(
        key,
    ):  # nflverse uses NULLs for not-applicable; treat as 0 (observed zero-stat week)
        v = row[key]
        return 0.0 if v is None else float(v)

    return StatLine(
        pass_completions=n("completions"),
        pass_incompletions=n("attempts") - n("completions"),
        pass_yards=n("passing_yards"),
        pass_tds=n("passing_tds"),
        interceptions=n("interceptions"),
        rush_attempts=n("carries"),
        rush_yards=n("rushing_yards"),
        rush_tds=n("rushing_tds"),
        rush_first_downs=n("rushing_first_downs"),
        receptions=n("receptions"),
        rec_yards=n("receiving_yards"),
        rec_tds=n("receiving_tds"),
        rec_first_downs=n("receiving_first_downs"),
        return_yards=n("punt_return_yards") + n("kickoff_return_yards"),
        return_tds=n("special_teams_tds"),
        two_point_conversions=n("two_point_conversions"),
        fumbles=n("fumbles"),
        fumbles_lost=n("fumbles_lost"),
        # pick_sixes / offensive_fumble_return_tds: KNOWN_GAPS — stay None.
    )
