import json

import psycopg2.extras
import pytest

import import_draft_log
from ffi.draft.state import DraftEvent, DraftLog, TornTailError


def test_append_replay_roundtrip(tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    e1 = log.append("meta", {"league_key": "nfl.l.123", "our_franchise_slot": 5})
    e2 = log.append("pick", {"overall": 1, "round": 1, "franchise_slot": 3})
    e3 = log.append("mode", {"from": "LIVE", "to": "MANUAL", "reason": "poll fail"})

    _, events, torn_tail = DraftLog.replay(path)

    assert events == [e1, e2, e3]
    assert torn_tail is False
    assert [e.seq for e in events] == [1, 2, 3]


def test_seq_strictly_increasing_across_resume(tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})
    log.append("pick", {"overall": 1})

    resumed_log, events, torn_tail = DraftLog.replay(path)
    assert torn_tail is False
    assert [e.seq for e in events] == [1, 2]

    e3 = resumed_log.append("pick", {"overall": 2})
    assert e3.seq == 3

    _, events2, torn_tail2 = DraftLog.replay(path)
    assert [e.seq for e in events2] == [1, 2, 3]
    assert torn_tail2 is False


def test_torn_final_line_dropped_and_flagged(tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})
    log.append("pick", {"overall": 1})

    with open(path, "ab") as f:
        f.write(b'{"seq": 3, "ts"')  # no trailing newline -- torn mid-write

    _, events, torn_tail = DraftLog.replay(path)
    assert len(events) == 2
    assert torn_tail is True


def test_corrupt_middle_line_raises(tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})

    with open(path, "a") as f:
        f.write("not valid json at all\n")

    log.append("pick", {"overall": 1})

    with pytest.raises(TornTailError):
        DraftLog.replay(path)


def test_undo_is_an_append_not_a_rewrite(tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})
    log.append("pick", {"overall": 1})

    lines_before = path.read_text().count("\n")

    log.append("undo", {"undoes_seq": 2})

    lines_after = path.read_text().count("\n")
    assert lines_after == lines_before + 1

    _, events, torn_tail = DraftLog.replay(path)
    assert torn_tail is False
    assert [e.kind for e in events] == ["meta", "pick", "undo"]


def test_draft_events_migration_applied(db):
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "INSERT INTO draft.events (draft_id, seq, ts, kind, payload)"
            " VALUES (%s, %s, now(), %s, %s)",
            ("2026-rehearsal-1", 1, "meta", json.dumps({"league_key": "nfl.l.123"})),
        )
        cur.execute(
            "SELECT draft_id, seq, kind, payload FROM draft.events WHERE draft_id = %s",
            ("2026-rehearsal-1",),
        )
        row = cur.fetchone()

    assert row["draft_id"] == "2026-rehearsal-1"
    assert row["seq"] == 1
    assert row["kind"] == "meta"
    assert row["payload"] == {"league_key": "nfl.l.123"}


def test_importer_inserts_all_events(db, tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})
    log.append("pick", {"overall": 1, "round": 1})
    log.append("mode", {"from": "LIVE", "to": "MANUAL", "reason": "test"})

    count = import_draft_log.import_log(db, path, "2026-rehearsal-2", replace=False)

    assert count == 3
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM draft.events WHERE draft_id = %s",
            ("2026-rehearsal-2",),
        )
        assert cur.fetchone()[0] == 3


def test_importer_refuses_without_replace(db, tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})

    import_draft_log.import_log(db, path, "2026-rehearsal-3", replace=False)

    with pytest.raises(SystemExit):
        import_draft_log.import_log(db, path, "2026-rehearsal-3", replace=False)


def test_importer_replace_overwrites(db, tmp_path):
    path = tmp_path / "draft.jsonl"
    log = DraftLog(path)
    log.append("meta", {"league_key": "nfl.l.123"})

    import_draft_log.import_log(db, path, "2026-rehearsal-4", replace=False)

    log.append("pick", {"overall": 1, "round": 1})
    count = import_draft_log.import_log(db, path, "2026-rehearsal-4", replace=True)

    assert count == 2
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM draft.events WHERE draft_id = %s",
            ("2026-rehearsal-4",),
        )
        assert cur.fetchone()[0] == 2
