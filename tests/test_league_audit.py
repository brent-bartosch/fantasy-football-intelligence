import pytest

from audit_league_history import parse_settings, renew_to_league_key


def test_renew_pointer_converts_to_league_key():
    assert renew_to_league_key("449_389359") == "449.l.389359"
    assert renew_to_league_key("") is None
    assert renew_to_league_key(None) is None


def test_parse_settings_extracts_qb_slots():
    settings = {
        "name": "NAJEE 'LEFT EYE' HARRIS",
        "season": "2025",
        "num_teams": 12,
        "renew": "449_123456",
        "renewed": "",
        "roster_positions": [
            {"roster_position": {"position": "QB", "count": 2}},
            {"roster_position": {"position": "WR", "count": 3}},
            {"roster_position": {"position": "BN", "count": 8}},
        ],
    }
    row = parse_settings("461.l.326814", settings)
    assert row["qb_slots"] == 2
    assert row["season"] == 2025
    assert row["num_teams"] == 12
    assert row["renew"] == "449_123456"


def test_parse_settings_fails_loud_on_missing_roster():
    with pytest.raises(KeyError):
        parse_settings(
            "461.l.326814",
            {"name": "x", "season": "2025", "num_teams": 12, "renew": ""},
        )


def test_extract_managers_handles_both_yahoo_shapes():
    from audit_league_history import extract_managers

    teams = {
        "461.t.1": {
            "managers": [{"manager": {"guid": "ABC123", "nickname": "Sports"}}]
        },
        "461.t.2": {"managers": {"manager": {"guid": "DEF456", "nickname": "Mike"}}},
    }
    assert extract_managers(teams) == {"ABC123": "Sports", "DEF456": "Mike"}


def test_extract_managers_treats_hidden_sentinel_as_no_guid():
    # REAL observed shape (2026): Yahoo returns the literal string "--hidden--"
    # for every manager's guid, including the authenticated user's own team.
    # Must not collapse distinct managers onto one dict key.
    from audit_league_history import extract_managers

    teams = {
        "461.t.1": {
            "managers": [
                {
                    "manager": {
                        "guid": "--hidden--",
                        "nickname": "Solis",
                        "manager_id": "7",
                    }
                }
            ]
        },
        "461.t.2": {
            "managers": [
                {
                    "manager": {
                        "guid": "--hidden--",
                        "nickname": "Brent",
                        "manager_id": "12",
                        "is_current_login": "1",
                    }
                }
            ]
        },
    }
    result = extract_managers(teams)
    assert len(result) == 2
    assert result["no-guid:7"] == "Solis"
    assert result["no-guid:12"] == "Brent"
