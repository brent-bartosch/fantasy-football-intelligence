"""Rehearsal-drill fake transports (Phase 4 / Task 17).

`scripts/drill_draft.py` drives the REAL `DraftSession` against a replay of a
historical Yahoo draft. The pacing/fault mechanism (`_fetch_closure`) is a pure
function of an ordered pick list + a fake clock, so it is asserted here with no
DB and no wall clock (the list-of-payload + FakeClock pattern from
`test_draft_session.py`); one DB-backed test pins the `draft_picks` -> payload
mapping (bare Yahoo id, team_key, round -- Fact 11: unmade picks lack
`player_id`).
"""
import time

import pytest

from drill_draft import _fetch_closure, scripted_fetch
from ffi.yahoo_client import YahooAuthError, YahooRateLimitError


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _picks(n):
    """n made picks, all in round 1, team_key = t.<snake position>, a bare
    numeric player_id."""
    return [
        {
            "pick": i,
            "round": 1,
            "team_key": f"461.l.1.t.{((i - 1) % 12) + 1}",
            "player_id": str(1000 + i),
        }
        for i in range(1, n + 1)
    ]


def _made(payload):
    return [p for p in payload if "player_id" in p]


def test_releases_picks_progressively_on_fake_clock():
    clock = FakeClock()
    fetch = _fetch_closure(_picks(12), {}, clock=clock, cadence_s=1.0)

    # First fetch establishes t0 and releases only pick 1.
    assert [p["pick"] for p in _made(fetch())] == [1]

    clock.advance(2.0)  # two cadence steps -> picks 1,2,3 visible
    assert [p["pick"] for p in _made(fetch())] == [1, 2, 3]

    clock.advance(100.0)  # everything visible
    assert [p["pick"] for p in _made(fetch())] == list(range(1, 13))


def test_unmade_picks_lack_player_id_full_snapshot():
    """Fact 11: Yahoo returns EVERY slot; unmade ones simply lack player_id.
    The transport mirrors that -- the full snapshot is always returned, only
    released picks carry a player_id."""
    clock = FakeClock()
    fetch = _fetch_closure(_picks(12), {}, clock=clock, cadence_s=1.0)

    fetch()  # anchor t0 at the first call
    clock.advance(2.0)  # picks 1,2,3 released
    payload = fetch()

    assert len(payload) == 12  # all slots present
    assert [p["pick"] for p in payload if "player_id" in p] == [1, 2, 3]
    assert [p["pick"] for p in payload if "player_id" not in p] == list(range(4, 13))


def test_injects_auth_failure_at_pick_then_recovers():
    clock = FakeClock()
    fetch = _fetch_closure(_picks(12), {"fail_at": [3]}, clock=clock, cadence_s=1.0)

    fetch()  # t0 set, pick 1 visible
    clock.advance(2.0)  # frontier reaches pick 3 -> the fault fires

    with pytest.raises(YahooAuthError):
        fetch()

    # A single failure: the very next fetch succeeds and delivers pick 3.
    payload = fetch()
    assert 3 in [p["pick"] for p in _made(payload)]


def test_injects_999_at_pick():
    clock = FakeClock()
    fetch = _fetch_closure(_picks(12), {"rate_limit_at": 5}, clock=clock, cadence_s=1.0)

    fetch()
    clock.advance(10.0)  # frontier passes pick 5

    with pytest.raises(YahooRateLimitError):
        fetch()


def test_latency_injection_sleeps_on_wall_clock():
    clock = FakeClock()
    fetch = _fetch_closure(
        _picks(3), {"latency_s": {1: 0.05}}, clock=clock, cadence_s=0.0
    )
    start = time.monotonic()
    fetch()  # pick 1 arrives with a 0.05s latency spike
    assert time.monotonic() - start >= 0.05


def test_release_time_exposed_for_lag_measurement():
    clock = FakeClock(2000.0)
    fetch = _fetch_closure(_picks(12), {}, clock=clock, cadence_s=2.5)
    fetch()  # sets t0
    # pick k becomes visible at t0 + (k-1)*cadence
    assert fetch.release_time(1) == 2000.0
    assert fetch.release_time(5) == 2000.0 + 4 * 2.5
    assert fetch.total == 12


# --- DB-backed: draft_picks -> payload mapping (bare yahoo id) ---


def test_scripted_fetch_maps_draft_picks_to_bare_yahoo_ids(db):
    league_key = "449.l.111"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO leagues (league_id, season_year, num_teams) "
            "VALUES (%s, 2024, 12)",
            (league_key,),
        )
        team_ids = {}
        for slot in range(1, 13):
            cur.execute(
                "INSERT INTO teams (league_id, slot, team_key) "
                "VALUES (%s, %s, %s) RETURNING team_id",
                (league_key, slot, f"{league_key}.t.{slot}"),
            )
            team_ids[slot] = cur.fetchone()[0]
        # Two round-1 picks: player yahoo ids stored as full player keys
        # (449.p.<bare>), the bare tail is what the live poller resolves.
        specs = [
            (1, 1, 1, "449.p.100", "Josh Allen", "QB"),
            (2, 1, 2, "449.p.200", "Bills", "DEF"),
        ]
        for overall, rnd, slot, ykey, name, pos in specs:
            cur.execute(
                "INSERT INTO players (yahoo_player_id, player_name, position) "
                "VALUES (%s, %s, %s) RETURNING player_id",
                (ykey, name, pos),
            )
            pid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO draft_picks "
                "(league_id, team_id, player_id, round_number, overall_pick) "
                "VALUES (%s, %s, %s, %s, %s)",
                (league_key, team_ids[slot], pid, rnd, overall),
            )
    db.commit()

    fetch = scripted_fetch(db, league_key, 2024, {}, clock=FakeClock(), cadence_s=0.0)
    payload = fetch()

    by_pick = {p["pick"]: p for p in payload}
    assert by_pick[1]["player_id"] == "100"  # bare id, prefix stripped
    assert by_pick[1]["team_key"] == f"{league_key}.t.1"
    assert by_pick[1]["round"] == 1
    assert by_pick[2]["player_id"] == "200"
    assert by_pick[2]["team_key"] == f"{league_key}.t.2"


def test_scripted_fetch_rejects_season_mismatch(db):
    league_key = "449.l.222"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO leagues (league_id, season_year, num_teams) "
            "VALUES (%s, 2024, 12)",
            (league_key,),
        )
    db.commit()

    with pytest.raises(ValueError):
        scripted_fetch(db, league_key, 2023, {}, clock=FakeClock())  # wrong season
