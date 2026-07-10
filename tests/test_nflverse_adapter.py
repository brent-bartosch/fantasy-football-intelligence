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
    "fg_made_0_19": None,
    "fg_made_20_29": None,
    "fg_made_30_39": None,
    "fg_made_40_49": None,
    "fg_made_50_plus": None,
    "fg_missed_0_19": None,
    "fg_missed_20_29": None,
    "fg_missed_30_39": None,
    "pat_made": None,
    "pat_missed": None,
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


K_ROW = dict(
    ROW,
    position="K",
    fg_made_0_19=1,
    fg_made_20_29=2,
    fg_made_30_39=0,
    fg_made_40_49=1,
    fg_made_50_plus=1,
    fg_missed_0_19=0,
    fg_missed_20_29=1,
    fg_missed_30_39=0,
    pat_made=3,
    pat_missed=1,
)


def test_kicker_row_maps_fg_and_pat_fields():
    line = stat_line_from_nflverse(K_ROW)
    assert line.fg_0_19 == 1
    assert line.fg_20_29 == 2
    assert line.fg_30_39 == 0
    assert line.fg_40_49 == 1
    assert line.fg_50_plus == 1
    assert line.fg_miss_0_19 == 0
    assert line.fg_miss_20_29 == 1
    assert line.fg_miss_30_39 == 0
    assert line.pat_made == 3
    assert line.pat_missed == 1
