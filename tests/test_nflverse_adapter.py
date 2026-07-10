from ffi.scoring.nflverse_adapter import KNOWN_GAPS, stat_line_from_nflverse

ROW = {
    "gsis_id": "00-0039075",
    "season": 2025,
    "week": 16,
    "player_name": "X",
    "position": "WR",
    "team": "BAL",
    "completions": 0,
    "attempts": 0,
    "passing_yards": 0.0,
    "passing_tds": 0,
    "passing_first_downs": 0,
    "interceptions": 0,
    "carries": 2,
    "rushing_yards": 8.0,
    "rushing_tds": 0,
    "rushing_first_downs": 0,
    "receptions": 7,
    "targets": 9,
    "receiving_yards": 143.0,
    "receiving_tds": 1,
    "receiving_first_downs": 5,
    "punt_return_yards": 12.0,
    "kickoff_return_yards": 20.0,
    "fumbles_lost": 0,
    "fumbles": 1,
    "two_point_conversions": 0,
    "special_teams_tds": 0,
}


def test_mapping_and_derivations():
    line = stat_line_from_nflverse(ROW)
    assert line.receptions == 7
    assert line.rec_first_downs == 5
    assert line.rush_attempts == 2
    assert line.pass_incompletions == 0  # attempts - completions
    assert line.return_yards == 32.0  # punt + kickoff
    assert line.return_tds == 0
    assert line.fumbles == 1


def test_incompletions_derived():
    row = dict(ROW, attempts=30, completions=20)
    assert stat_line_from_nflverse(row).pass_incompletions == 10


def test_known_gaps_documented():
    line = stat_line_from_nflverse(ROW)
    assert line.pick_sixes is None  # not in nflverse
    assert "pick_sixes" in KNOWN_GAPS
    assert "offensive_fumble_return_tds" in KNOWN_GAPS
