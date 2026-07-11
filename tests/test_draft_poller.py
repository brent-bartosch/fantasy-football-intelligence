import json

import pytest

from ffi.draft.poller import (
    DraftPoller,
    PollResult,
    ResolvedPick,
    build_resolver,
    load_team_slots,
)
from ffi.draft.state import DraftLog
from ffi.yahoo_client import YahooRateLimitError

TEAM_SLOTS = {
    "461.l.1.t.1": 1,
    "461.l.1.t.2": 2,
    "461.l.1.t.3": 3,
}


def _resolve_ok(yahoo_player_id: str):
    table = {"100": ("6794", "Justin Jefferson", "WR")}
    return table.get(yahoo_player_id)


def _resolve_miss(yahoo_player_id: str):
    return None


def _pick(pick, round_, team_key, player_id=None):
    d = {"pick": pick, "round": round_, "team_key": team_key}
    if player_id is not None:
        d["player_id"] = player_id
    return d


def test_unmade_picks_ignored(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")
    picks = [
        _pick(1, 1, "461.l.1.t.1", player_id="100"),
        _pick(2, 1, "461.l.1.t.2"),  # unmade -- no player_id
    ]
    poller = DraftPoller(lambda: picks, _resolve_ok, TEAM_SLOTS, log)

    result = poller.poll()

    assert [rp.overall for rp in result.new_picks] == [1]
    assert result.total_made == 1


def test_diff_only_new_picks(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")
    picks = [_pick(1, 1, "461.l.1.t.1", player_id="100")]
    poller = DraftPoller(lambda: picks, _resolve_ok, TEAM_SLOTS, log)

    first = poller.poll()
    second = poller.poll()

    assert [rp.overall for rp in first.new_picks] == [1]
    assert second.new_picks == ()
    assert second.total_made == 1


def test_out_of_order_and_resend_idempotent(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")
    state = {
        "picks": [
            _pick(2, 1, "461.l.1.t.2", player_id="100"),
            _pick(1, 1, "461.l.1.t.1", player_id="100"),
        ]
    }
    poller = DraftPoller(lambda: state["picks"], _resolve_ok, TEAM_SLOTS, log)

    first = poller.poll()
    assert [rp.overall for rp in first.new_picks] == [
        1,
        2,
    ]  # sorted despite arrival order

    # Re-send of the same payload plus one genuinely new pick, arriving
    # out of order relative to pick number.
    state["picks"] = [
        _pick(3, 1, "461.l.1.t.3", player_id="100"),
        _pick(1, 1, "461.l.1.t.1", player_id="100"),
        _pick(2, 1, "461.l.1.t.2", player_id="100"),
    ]
    second = poller.poll()
    assert [rp.overall for rp in second.new_picks] == [3]


def test_crosswalk_miss_yields_ref_none(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")
    picks = [_pick(1, 1, "461.l.1.t.1", player_id="999999")]
    poller = DraftPoller(lambda: picks, _resolve_miss, TEAM_SLOTS, log)

    result = poller.poll()

    assert len(result.new_picks) == 1
    rp = result.new_picks[0]
    assert isinstance(rp, ResolvedPick)
    assert rp.ref is None
    assert rp.name is None
    assert rp.pos is None

    _, events, _ = DraftLog.replay(log.path)
    pick_events = [e for e in events if e.kind == "pick"]
    assert pick_events[-1].payload["ref"] is None


def test_rate_limit_propagates(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")

    def boom():
        raise YahooRateLimitError("999")

    poller = DraftPoller(boom, _resolve_ok, TEAM_SLOTS, log)

    with pytest.raises(YahooRateLimitError):
        poller.poll()


def test_picks_logged_before_returned(tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    picks = [_pick(1, 1, "461.l.1.t.1", player_id="100")]
    poller = DraftPoller(lambda: picks, _resolve_ok, TEAM_SLOTS, log)

    result = poller.poll()

    assert result.new_picks[0].overall == 1
    _, events, torn_tail = DraftLog.replay(path)
    assert torn_tail is False
    pick_events = [e for e in events if e.kind == "pick"]
    assert len(pick_events) == 1
    assert pick_events[0].payload == {
        "overall": 1,
        "round": 1,
        "franchise_slot": 1,
        "team_key": "461.l.1.t.1",
        "ref": "6794",
        "yahoo_player_id": "100",
        "name": "Justin Jefferson",
        "pos": "WR",
        "source": "poll",
    }


def test_unknown_team_key_raises(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")
    picks = [_pick(1, 1, "461.l.1.t.99", player_id="100")]  # not in TEAM_SLOTS
    poller = DraftPoller(lambda: picks, _resolve_ok, TEAM_SLOTS, log)

    with pytest.raises(ValueError):
        poller.poll()

    # Nothing should have been logged for the corrupt pick.
    _, events, _ = DraftLog.replay(log.path)
    assert [e for e in events if e.kind == "pick"] == []


def test_earlier_valid_picks_in_batch_stay_logged_before_bad_one_raises(tmp_path):
    log = DraftLog(tmp_path / "draft.jsonl")
    picks = [
        _pick(1, 1, "461.l.1.t.1", player_id="100"),  # valid
        _pick(2, 1, "461.l.1.t.99", player_id="100"),  # unknown team_key
    ]
    poller = DraftPoller(lambda: picks, _resolve_ok, TEAM_SLOTS, log)

    with pytest.raises(ValueError):
        poller.poll()

    _, events, _ = DraftLog.replay(log.path)
    pick_events = [e for e in events if e.kind == "pick"]
    assert [e.payload["overall"] for e in pick_events] == [1]


# --- load_team_slots / build_resolver: DB-backed ---


def test_load_team_slots_maps_team_key_to_slot(db):
    league_key = "461.l.999"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO leagues (league_id, season_year, num_teams) VALUES (%s, 2026, 12)",
            (league_key,),
        )
        for slot in range(1, 13):
            cur.execute(
                "INSERT INTO teams (league_id, slot, team_key) VALUES (%s, %s, %s)",
                (league_key, slot, f"{league_key}.t.{slot}"),
            )
    db.commit()

    mapping = load_team_slots(db, league_key)

    assert mapping == {f"{league_key}.t.{slot}": slot for slot in range(1, 13)}


def test_load_team_slots_wrong_count_raises(db):
    league_key = "461.l.998"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO leagues (league_id, season_year, num_teams) VALUES (%s, 2026, 12)",
            (league_key,),
        )
        for slot in range(1, 12):  # only 11
            cur.execute(
                "INSERT INTO teams (league_id, slot, team_key) VALUES (%s, %s, %s)",
                (league_key, slot, f"{league_key}.t.{slot}"),
            )
    db.commit()

    with pytest.raises(ValueError):
        load_team_slots(db, league_key)


def test_build_resolver_matches_by_yahoo_id(db):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO public.player_id_xwalk (name, position, sleeper_id, yahoo_id)
               VALUES ('Justin Jefferson', 'WR', '6794', '32692')"""
        )
    db.commit()

    resolve = build_resolver(db)

    assert resolve("32692") == ("6794", "Justin Jefferson", "WR")
    assert resolve("00000") is None


def test_build_resolver_includes_def_rows(db):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO public.player_id_xwalk (name, position, sleeper_id, yahoo_id, manual_override)
               VALUES ('Rams DEF', 'DEF', 'LAR', '100012', true)"""
        )
    db.commit()

    resolve = build_resolver(db)

    assert resolve("100012") == ("LAR", "Rams DEF", "DEF")
