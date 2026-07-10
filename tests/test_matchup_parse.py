from ffi.history.matchups import parse_matchup_payload


def _team(team_key, pts, proj):
    return [
        [
            {"team_key": team_key},
            {"team_id": team_key.rsplit(".t.", 1)[1]},
            {"name": "X"},
        ],
        {
            "team_points": {"week": "1", "total": str(pts), "coverage_type": "week"},
            "team_projected_points": {
                "week": "1",
                "total": str(proj),
                "coverage_type": "week",
            },
        },
    ]


def _payload(pairs, is_playoffs="0"):
    matchups = {
        str(i): {
            "matchup": {
                "is_playoffs": is_playoffs,
                "0": {
                    "teams": {
                        "0": {"team": _team(a, pa, pra)},
                        "1": {"team": _team(b, pb, prb)},
                        "count": 2,
                    }
                },
            }
        }
        for i, (a, pa, pra, b, pb, prb) in enumerate(pairs)
    }
    matchups["count"] = len(pairs)
    return {
        "fantasy_content": {
            "league": [
                {"league_key": "461.l.326814"},
                {"scoreboard": {"0": {"matchups": matchups}, "week": "1"}},
            ]
        }
    }


def test_parse_two_matchups():
    payload = _payload(
        [
            ("461.l.326814.t.1", 227.75, 219.67, "461.l.326814.t.2", 190.0, 200.0),
            ("461.l.326814.t.3", 150.5, 160.0, "461.l.326814.t.4", 151.0, 140.0),
        ]
    )
    rows = parse_matchup_payload(payload)
    assert len(rows) == 4  # one row per team-side
    r1 = next(r for r in rows if r["team_key"].endswith(".t.1"))
    assert r1["points"] == 227.75
    assert r1["opp_team_key"].endswith(".t.2")
    assert r1["opp_points"] == 190.0
    assert r1["is_playoffs"] is False


def test_parse_fails_loud_on_missing_points():
    payload = _payload([("461.l.326814.t.1", 1, 1, "461.l.326814.t.2", 2, 2)])
    del payload["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]["0"][
        "matchup"
    ]["0"]["teams"]["0"]["team"][1]["team_points"]
    import pytest

    with pytest.raises(KeyError):
        parse_matchup_payload(payload)
