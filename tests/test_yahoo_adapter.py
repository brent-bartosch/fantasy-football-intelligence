import pytest

from ffi.ingest.base import IngestError
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo

OFFENSE_PAYLOAD = {
    "Fum": 0.0,
    "Inc": 0.0,
    "Int": 0.0,
    "Rec": 7.0,
    "2-PT": 0.0,
    "Comp": 0.0,
    "name": "Zay Flowers",
    "Rec TD": 1.0,
    "Ret TD": 0.0,
    "Pass TD": 0.0,
    "Rec Yds": 143.0,
    "Ret Yds": 0.0,
    "Rush TD": 0.0,
    "Targets": 9.0,
    "Fum Lost": 0.0,
    "Pass Yds": 0.0,
    "Pick Six": 0.0,
    "Rush Att": 2.0,
    "Rush Yds": 8.0,
    "player_id": 40039,
    "Fum Ret TD": 0.0,
    "total_points": "36.76",
    "Rec 1st Downs": 5.0,
    "position_type": "O",
    "Rush 1st Downs": 0.0,
}


def test_offense_mapping():
    line = stat_line_from_yahoo(OFFENSE_PAYLOAD)
    assert line.receptions == 7.0
    assert line.rec_yards == 143.0
    assert line.rec_first_downs == 5.0
    assert line.rush_attempts == 2.0
    assert line.fg_0_19 is None  # kicking fields untouched for offense
    assert line.points_allowed is None


def test_unknown_key_fails_loud():
    payload = dict(OFFENSE_PAYLOAD, **{"40+ Yd Comp": 2.0})
    with pytest.raises(IngestError, match="unmapped"):
        stat_line_from_yahoo(payload)


def test_missing_position_type_fails_loud():
    payload = {k: v for k, v in OFFENSE_PAYLOAD.items() if k != "position_type"}
    with pytest.raises(IngestError, match="position_type"):
        stat_line_from_yahoo(payload)


DEF_PAYLOAD = {
    "TD": 0.0,
    "Int": 0.0,
    "TFL": 6.0,
    "XPR": 0.0,
    "Sack": 3.0,
    "Safe": 0.0,
    "name": "Chiefs",
    "Fum Rec": 0.0,
    "Blk Kick": 0.0,
    "Pts Allow": 27.0,
    "player_id": 100012,
    "3 and Outs": 2.0,
    "4 Dwn Stops": 0.0,
    "Pts Allow 0": 0.0,
    "total_points": "11.00",
    "Def Yds Allow": 394.0,
    "Pts Allow 1-6": 0.0,
    "Pts Allow 35+": 0.0,
    "Yds Allow Neg": 0.0,
    "position_type": "DT",
    "Pts Allow 7-13": 0.0,
    "Yds Allow 0-99": 0.0,
    "Yds Allow 500+": 0.0,
    "Pts Allow 14-20": 0.0,
    "Pts Allow 21-27": 1.0,
    "Pts Allow 28-34": 0.0,
    "Yds Allow 100-199": 0.0,
    "Yds Allow 200-299": 0.0,
    "Yds Allow 300-399": 1.0,
    "Yds Allow 400-499": 0.0,
}


def test_def_mapping_uses_raw_values():
    line = stat_line_from_yahoo(DEF_PAYLOAD)
    assert line.points_allowed == 27.0
    assert line.yards_allowed == 394.0
    assert line.tackles_for_loss == 6.0
    assert line.def_interceptions == 0.0


def test_def_tier_indicator_cross_check_fails_on_mismatch():
    bad = dict(DEF_PAYLOAD, **{"Pts Allow 21-27": 0.0, "Pts Allow 14-20": 1.0})
    with pytest.raises(IngestError, match="tier indicator"):
        stat_line_from_yahoo(bad)


K_PAYLOAD = {
    "name": "Brandon Aubrey",
    "FG 50+": 1.0,
    "FG 0-19": 0.0,
    "FG 20-29": 0.0,
    "FG 30-39": 0.0,
    "FG 40-49": 1.0,
    "FGM 0-19": 0.0,
    "PAT Made": 2.0,
    "PAT Miss": 0.0,
    "FGM 20-29": 0.0,
    "FGM 30-39": 0.0,
    "player_id": 40819,
    "total_points": "11.00",
    "position_type": "K",
}


def test_kicker_mapping():
    line = stat_line_from_yahoo(K_PAYLOAD)
    assert line.fg_50_plus == 1.0
    assert line.fg_40_49 == 1.0
    assert line.pat_made == 2.0
    assert line.fg_miss_0_19 == 0.0
