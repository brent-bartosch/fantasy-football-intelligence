"""Single home for Yahoo key parsing and NFL team-abbr normalization.

Yahoo key shapes:
  player: '{game}.p.{player_id}'   e.g. '461.p.40039'
  league: '{game}.l.{league_id}'   e.g. '461.l.326814'
  team:   '{game}.l.{league_id}.t.{slot}' e.g. '461.l.326814.t.7'

Game code changes every season (461=2025, 449=2024, ...), so the same NFL
player appears under multiple player keys — see v_player_yahoo_ids.
"""
import re


class IdParseError(ValueError):
    """A Yahoo key or team abbreviation didn't match the expected shape. Never guess."""


_PLAYER_KEY = re.compile(r"^(\d+)\.p\.(\d+)$")
_LEAGUE_KEY = re.compile(r"^(\d+)\.l\.(\d+)$")
_TEAM_KEY = re.compile(r"^(\d+)\.l\.(\d+)\.t\.(\d+)$")


def player_numeric_id(key: str) -> str:
    """'461.p.40039' -> '40039'. Bare numeric ids pass through.
    Legacy slug keys ('nfl.p.patrick_mahomes') raise IdParseError."""
    if key.isdigit():
        return key
    m = _PLAYER_KEY.match(key)
    if not m:
        raise IdParseError(f"not a numeric Yahoo player key: {key!r}")
    return m.group(2)


def is_numeric_player_key(key: str) -> bool:
    return key.isdigit() or _PLAYER_KEY.match(key) is not None


def player_key(game_code: str | int, player_id: str | int) -> str:
    return f"{game_code}.p.{player_id}"


def league_game_code(league_key: str) -> str:
    m = _LEAGUE_KEY.match(league_key)
    if not m:
        raise IdParseError(f"not a Yahoo league key: {league_key!r}")
    return m.group(1)


def team_slot(team_key: str) -> int:
    """'461.l.326814.t.7' -> 7. The slot is the stable per-season team number
    (manager identity anchor — see PROJECT-RECORD 13b)."""
    m = _TEAM_KEY.match(team_key)
    if not m:
        raise IdParseError(f"not a Yahoo team key: {team_key!r}")
    return int(m.group(3))


# The ONE definition of "numeric yahoo id" in SQL. Keep in sync with
# player_numeric_id above.
def yahoo_numeric_id_sql(col: str) -> str:
    return f"split_part({col}, '.p.', 2)"


def yahoo_numeric_id_filter_sql(col: str) -> str:
    return f"split_part({col}, '.p.', 2) ~ '^[0-9]+$'"


# Canonical = nflverse uppercase abbreviations (2025 franchises).
NFL_TEAMS = frozenset(
    {
        "ARI",
        "ATL",
        "BAL",
        "BUF",
        "CAR",
        "CHI",
        "CIN",
        "CLE",
        "DAL",
        "DEN",
        "DET",
        "GB",
        "HOU",
        "IND",
        "JAX",
        "KC",
        "LA",
        "LAC",
        "LV",
        "MIA",
        "MIN",
        "NE",
        "NO",
        "NYG",
        "NYJ",
        "PHI",
        "PIT",
        "SEA",
        "SF",
        "TB",
        "TEN",
        "WAS",
    }
)
# Yahoo/other-source spellings and relocated-franchise codes. NOTE: OAK/SD/STL
# map to the current franchise — fine for 2020+ joins; do not use this for
# pre-relocation era analysis.
_ALIASES = {
    "JAC": "JAX",
    "LAR": "LA",
    "WSH": "WAS",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
}


def normalize_team_abbr(abbr: str) -> str:
    """'Buf' -> 'BUF', 'JAC' -> 'JAX'. Unknown abbreviations fail loud."""
    up = abbr.strip().upper()
    up = _ALIASES.get(up, up)
    if up not in NFL_TEAMS:
        raise IdParseError(f"unknown NFL team abbreviation: {abbr!r}")
    return up
