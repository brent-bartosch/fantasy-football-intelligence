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
