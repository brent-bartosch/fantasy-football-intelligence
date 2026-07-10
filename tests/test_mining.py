from ffi.history.mining import all_play_from_weeks, roster_intervals


def test_all_play_from_weeks():
    # week scores: A=100, B=90, C=80 -> A beats both (2-0), B 1-1, C 0-2
    weeks = [
        {"week": 1, "scores": {"A": 100.0, "B": 90.0, "C": 80.0}},
        {"week": 2, "scores": {"A": 50.0, "B": 90.0, "C": 80.0}},
    ]
    ap = all_play_from_weeks(weeks)
    assert ap["A"] == {"wins": 2, "losses": 2}
    assert ap["B"] == {"wins": 3, "losses": 1}
    assert ap["C"] == {"wins": 1, "losses": 3}


def test_roster_intervals_add_then_drop():
    events = [
        {"player_ref": "p1", "team_key": "T1", "type": "draft", "week": 0},
        {"player_ref": "p1", "team_key": "T1", "type": "drop", "week": 5},
        {"player_ref": "p1", "team_key": "T2", "type": "add", "week": 7},
    ]
    iv = roster_intervals(events, end_week=17)
    assert ("T1", 1, 5, "draft") in iv["p1"]  # on T1 weeks 1-5 via draft
    assert ("T2", 7, 17, "add") in iv["p1"]  # on T2 weeks 7-17 via add


def test_roster_intervals_trade_moves_player():
    events = [
        {"player_ref": "p1", "team_key": "T1", "type": "draft", "week": 0},
        {"player_ref": "p1", "team_key": "T2", "type": "trade_in", "week": 8},
    ]
    iv = roster_intervals(events, end_week=17)
    assert ("T1", 1, 8, "draft") in iv["p1"]
    assert ("T2", 8, 17, "trade_in") in iv["p1"]
