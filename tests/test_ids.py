import pytest
from ffi.ids import (
    IdParseError,
    is_numeric_player_key,
    league_game_code,
    normalize_team_abbr,
    player_key,
    player_numeric_id,
    team_slot,
    yahoo_numeric_id_filter_sql,
    yahoo_numeric_id_sql,
)


def test_player_numeric_id_from_full_key():
    assert player_numeric_id("461.p.40039") == "40039"


def test_player_numeric_id_bare_passthrough():
    assert player_numeric_id("40039") == "40039"


def test_player_numeric_id_rejects_legacy_slug():
    with pytest.raises(IdParseError):
        player_numeric_id("nfl.p.patrick_mahomes")


def test_player_numeric_id_rejects_league_key():
    with pytest.raises(IdParseError):
        player_numeric_id("461.l.326814")


def test_is_numeric_player_key():
    assert is_numeric_player_key("461.p.40039")
    assert is_numeric_player_key("40039")
    assert not is_numeric_player_key("nfl.p.patrick_mahomes")


def test_player_key_roundtrip():
    assert player_key("461", 40039) == "461.p.40039"
    assert player_numeric_id(player_key("461", 40039)) == "40039"


def test_league_game_code():
    assert league_game_code("461.l.326814") == "461"
    with pytest.raises(IdParseError):
        league_game_code("461.p.40039")


def test_team_slot():
    assert team_slot("461.l.326814.t.7") == 7
    with pytest.raises(IdParseError):
        team_slot("461.l.326814")


def test_normalize_team_abbr_yahoo_mixed_case():
    assert normalize_team_abbr("Buf") == "BUF"
    assert normalize_team_abbr("Was") == "WAS"
    assert normalize_team_abbr("Jax") == "JAX"


def test_normalize_team_abbr_aliases():
    assert normalize_team_abbr("JAC") == "JAX"
    assert normalize_team_abbr("LAR") == "LA"
    assert normalize_team_abbr("WSH") == "WAS"


def test_normalize_team_abbr_unknown_fails_loud():
    with pytest.raises(IdParseError):
        normalize_team_abbr("XYZ")


def test_sql_fragments():
    assert (
        yahoo_numeric_id_sql("p.yahoo_player_id")
        == "split_part(p.yahoo_player_id, '.p.', 2)"
    )
    assert "~ '^[0-9]+$'" in yahoo_numeric_id_filter_sql("yahoo_player_id")
