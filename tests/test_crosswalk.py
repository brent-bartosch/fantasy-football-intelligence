import pytest

from ffi.ingest.crosswalk import load_xwalk_rows, match_report


def _seed(db):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO public.player_id_xwalk (name, position, gsis_id, sleeper_id, yahoo_id, fantasypros_id)
                       VALUES ('Justin Jefferson','WR','00-0036322','6794','32692','19236')"""
        )
        cur.execute(
            """INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                       VALUES ('449.p.32692','Justin Jefferson','WR','MIN'),
                              ('449.p.99999','Mystery Man','RB','FA')"""
        )
    db.commit()


def test_match_report_flags_unmatched(db):
    _seed(db)
    report = match_report(db)
    assert report["total_fantasy_players"] == 2
    assert report["matched"] == 1
    assert report["unmatched"] == [("Mystery Man", "RB", "99999")]


def test_match_report_excludes_def_and_slug_rows(db):
    _seed(db)
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                       VALUES ('449.p.100001','Falcons','DEF','ATL'),
                              ('nfl.p.justin_jefferson','Justin Jefferson','WR','MIN')"""
        )
    db.commit()
    report = match_report(db)
    # DEF rows and legacy slug-format ids must not inflate the coverage denominator
    assert report["total_fantasy_players"] == 2
    assert report["matched"] == 1
    assert report["unmatched"] == [("Mystery Man", "RB", "99999")]
    # ...but both are surfaced explicitly
    assert report["def_rows"] == 1
    assert report["legacy_slug_rows"] == 1


from ffi.ingest.base import IngestError
from ffi.ingest.crosswalk import assert_no_duplicate_ids, dedupe_auto_vs_manual


def _insert_xwalk(db, name, yahoo_id, sleeper_id, manual):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, position, yahoo_id, sleeper_id, manual_override)"
            " VALUES (%s,'WR',%s,%s,%s)",
            (name, yahoo_id, sleeper_id, manual),
        )
    db.commit()


def test_manual_override_wins_over_auto_row(db):
    _insert_xwalk(db, "Rookie Guy", "99991", "s1", True)
    _insert_xwalk(db, "Rookie Guy", "99991", "s2", False)  # auto row, same yahoo_id
    dedupe_auto_vs_manual(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.player_id_xwalk WHERE yahoo_id='99991'"
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT manual_override FROM public.player_id_xwalk WHERE yahoo_id='99991'"
        )
        assert cur.fetchone()[0] is True


def test_duplicate_yahoo_id_tripwire(db):
    _insert_xwalk(db, "A", "88880", "sa", False)
    _insert_xwalk(db, "B", "88880", "sb", False)  # two auto rows, same yahoo_id
    with pytest.raises(IngestError, match="duplicate"):
        assert_no_duplicate_ids(db)


from ffi.ingest.crosswalk import dedupe_identical_players, quarantine_conflicting_ids


def _insert_xwalk_full(db, name, position, gsis_id, sleeper_id, yahoo_id, manual):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk"
            " (name, position, gsis_id, sleeper_id, yahoo_id, manual_override)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (name, position, gsis_id, sleeper_id, yahoo_id, manual),
        )
    db.commit()


def test_dedupe_identical_players_collapses_same_human(db):
    # Same person, double-entered upstream under two positions — identical ids.
    _insert_xwalk_full(db, "Ray Agnew", "RB", "00-0000001", "s1", "28011", False)
    _insert_xwalk_full(db, "Ray Agnew", "DL", "00-0000001", "s1", "28011", False)
    deleted = dedupe_identical_players(db)
    assert deleted == 1
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.player_id_xwalk WHERE yahoo_id='28011'"
        )
        assert cur.fetchone()[0] == 1


def test_quarantine_conflicting_ids_removes_both_different_humans(db):
    # Different people erroneously sharing a gsis_id upstream — no yahoo/sleeper
    # overlap, so dedupe_identical_players would not touch these.
    _insert_xwalk_full(db, "Bobby McCray", "DE", "00-0022888", None, None, False)
    _insert_xwalk_full(db, "Jake Schum", "PN", "00-0022888", "1399", "26613", False)
    quarantined = quarantine_conflicting_ids(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.player_id_xwalk WHERE gsis_id='00-0022888'"
        )
        assert cur.fetchone()[0] == 0
    names = {q[2] for q in quarantined}
    assert names == {"Bobby McCray", "Jake Schum"}


def test_quarantine_never_touches_manual_rows(db):
    # A manual row sharing an id with an auto row: dedupe_auto_vs_manual removes
    # the auto row first, so quarantine_conflicting_ids has nothing left to do —
    # the manual row survives both.
    _insert_xwalk(db, "Researched Player", "77770", "sm", True)
    _insert_xwalk(db, "Researched Player", "77770", "sa", False)
    dedupe_auto_vs_manual(db)
    dedupe_identical_players(db)
    quarantined = quarantine_conflicting_ids(db)
    assert quarantined == []
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*), bool_and(manual_override) FROM public.player_id_xwalk"
            " WHERE yahoo_id='77770'"
        )
        count, all_manual = cur.fetchone()
        assert count == 1
        assert all_manual is True
