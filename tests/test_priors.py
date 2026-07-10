import pytest

from ffi.sim.priors import (
    POSITIONS,
    SlotPriors,
    _pos_share_from_rows,
    build_slot_priors,
)


def test_recency_weighting_prefers_recent_seasons():
    # slot 1 drafted QB round 1 in 2010-2017, WR round 1 in 2022-2025
    rows = [(1, s, 1, "QB") for s in range(2010, 2018)] + [
        (1, s, 1, "WR") for s in range(2022, 2026)
    ]
    share = _pos_share_from_rows(rows, floors={}, latest_season=2025)
    assert share[(1, 1)]["WR"] > share[(1, 1)]["QB"]


def test_annotation_floor_excludes_prior_human_seasons():
    rows = [(12, s, 1, "RB") for s in range(2010, 2022)] + [
        (12, s, 1, "QB") for s in range(2022, 2026)
    ]
    share = _pos_share_from_rows(rows, floors={12: 2022}, latest_season=2025)
    assert share[(12, 1)].get("RB", 0.0) < 0.05  # only band-shrinkage mass remains


def test_shares_sum_to_one_and_cover_all_rounds():
    # Two slots: slot 3 has direct data in every round 1-19 across 6 seasons;
    # slot 7 only has direct data in rounds 1 and 19 (band-shrinkage fallback
    # must still produce a full, valid distribution for its untouched rounds).
    rows = []
    positions_cycle = ["QB", "RB", "WR", "TE", "K", "DEF"]
    for season in range(2020, 2026):
        for rnd in range(1, 20):
            pos = positions_cycle[rnd % len(positions_cycle)]
            rows.append((3, season, rnd, pos))
        rows.append((7, season, 1, "QB"))
        rows.append((7, season, 19, "DEF"))

    share = _pos_share_from_rows(rows, floors={}, latest_season=2025)

    for slot in (3, 7):
        for rnd in range(1, 20):
            key = (slot, rnd)
            assert key in share, f"missing {key}"
            total = sum(share[key].values())
            assert total == pytest.approx(1.0, abs=1e-9), f"{key} sums to {total}"
            assert set(share[key]) == set(POSITIONS)


def test_unknown_position_fails_loud():
    with pytest.raises(ValueError, match="unexpected position"):
        _pos_share_from_rows([(1, 2025, 1, "W/R")], floors={}, latest_season=2025)


def test_league_wide_qb_share_exceeds_rb_wr_in_early_rounds():
    """Pure-data pin against the mining report's shape (§2/§3): this is a
    2QB league, so QB is taken unusually early league-wide. Synthetic rows
    mimic that shape (heavy early-round QB across many slots) rather than
    reading the live DB — the true historical-number check is deferred to
    the Task 12 sim-farm assumption audit per the brief."""
    rows = []
    for slot in range(1, 13):
        for season in range(2018, 2026):
            # Round 1: most slots take a QB; a few take RB/WR instead.
            rows.append((slot, season, 1, "QB" if slot <= 9 else "RB"))
            # Round 2: the remaining QB-needy slots grab their second QB.
            rows.append((slot, season, 2, "QB" if slot <= 7 else "WR"))
            # Later rounds: normal RB/WR-heavy skill-position drafting.
            rows.append((slot, season, 5, "RB"))
            rows.append((slot, season, 6, "WR"))

    share = _pos_share_from_rows(rows, floors={}, latest_season=2025)

    def league_wide(rnd, pos):
        return sum(share[(slot, rnd)][pos] for slot in range(1, 13)) / 12

    for rnd in (1, 2):
        qb = league_wide(rnd, "QB")
        rb = league_wide(rnd, "RB")
        wr = league_wide(rnd, "WR")
        assert qb > rb, f"round {rnd}: QB share {qb} not > RB share {rb}"
        assert qb > wr, f"round {rnd}: QB share {qb} not > WR share {wr}"


# --- Integration: build_slot_priors reads real tables + annotations ---


def _seed_league(db, league_id, season, slot_to_position):
    """One 12-team league-season: each slot's team drafts a single round-1
    player at the given position (enough to exercise the SQL reader + the
    floor join; band/round coverage isn't the point of this test)."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO leagues (league_id, season_year, num_teams) "
            "VALUES (%s, %s, 12) ON CONFLICT (league_id) DO NOTHING",
            (league_id, season),
        )
        cur.execute(
            "INSERT INTO raw.yahoo_league_settings (league_key, season, settings_payload) "
            "VALUES (%s, %s, %s::jsonb) ON CONFLICT (league_key) DO NOTHING",
            (league_id, season, "{}"),
        )
        for slot, position in slot_to_position.items():
            cur.execute(
                "INSERT INTO teams (league_id, slot, team_key) VALUES (%s, %s, %s) "
                "RETURNING team_id",
                (league_id, slot, f"{league_id}.t.{slot}"),
            )
            team_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO players (yahoo_player_id, player_name, position) "
                "VALUES (%s, %s, %s) RETURNING player_id",
                (f"{league_id}-{slot}", f"Player {league_id}-{slot}", position),
            )
            player_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO draft_picks (league_id, team_id, player_id, round_number, "
                "pick_number, overall_pick) VALUES (%s, %s, %s, %s, %s, %s)",
                (league_id, team_id, player_id, 1, slot, slot),
            )
    db.commit()


def test_build_slot_priors_reads_history_and_applies_floor(db):
    all_slots_qb = {slot: "QB" for slot in range(1, 13)}
    all_slots_rb = {slot: "RB" for slot in range(1, 13)}
    for season in range(2018, 2022):
        _seed_league(db, f"L{season}", season, all_slots_rb)
    for season in range(2022, 2026):
        _seed_league(db, f"L{season}", season, all_slots_qb)

    with db.cursor() as cur:
        # Slot 7 (not the migration-seeded slot 12) so this test's own insert
        # is the thing actually exercising the floor, not a pre-existing
        # ON-CONFLICT no-op against migration seed data.
        cur.execute(
            "INSERT INTO public.manager_slot_annotations "
            "(league_slot, human_label, from_season, note) "
            "VALUES (7, 'TestHuman', 2022, 'test seed') "
            "ON CONFLICT (league_slot, from_season) DO NOTHING"
        )
    db.commit()

    priors = build_slot_priors(db)

    assert isinstance(priors, SlotPriors)
    assert priors.latest_season == 2025
    assert priors.params["half_life"] == 4.0
    assert priors.params["shrink_m"] == 8.0
    assert priors.params["floors"].get(7) == 2022
    assert priors.params["n_picks_used"] == 12 * 8  # 12 slots x 8 seasons

    # Slot 7 is floored at 2022 — pre-2022 RB rows for slot 7 are excluded,
    # so its round-1 share should be QB-dominant.
    assert priors.pos_share[(7, 1)]["QB"] > priors.pos_share[(7, 1)]["RB"]

    # Slot 1 has no floor — both eras count, but 2022-2025 QB rows are more
    # recent (higher weight) than 2018-2021 RB rows, so QB still wins here.
    assert priors.pos_share[(1, 1)]["QB"] > priors.pos_share[(1, 1)]["RB"]

    # Every slot x round 1-19 key is present and sums to ~1.0.
    for slot in range(1, 13):
        for rnd in range(1, 20):
            total = sum(priors.pos_share[(slot, rnd)].values())
            assert total == pytest.approx(1.0, abs=1e-9)


def _insert_junk_picks(db, league_id, team_id, position, count):
    with db.cursor() as cur:
        for i in range(count):
            cur.execute(
                "INSERT INTO players (yahoo_player_id, player_name, position) "
                "VALUES (%s, %s, %s) RETURNING player_id",
                (f"junk-{league_id}-{i}", f"Junk {i}", position),
            )
            player_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO draft_picks (league_id, team_id, player_id, round_number, "
                "pick_number, overall_pick) VALUES (%s, %s, %s, 2, 1, 1)",
                (league_id, team_id, player_id),
            )
    db.commit()


def test_build_slot_priors_drops_rare_junk_position_with_disclosure(db, capsys):
    """<2% junk positions: dropped with a loud print, build still succeeds
    (fail-loud-with-disclosure — see module docstring). This branch is dead
    in production today (the real 3,720-pick history has zero junk
    positions, verified during this task), but it's a documented
    contingency and must actually work."""
    all_slots_qb = {slot: "QB" for slot in range(1, 13)}
    for season in range(2018, 2026):
        _seed_league(db, f"J{season}", season, all_slots_qb)
    with db.cursor() as cur:
        cur.execute(
            "SELECT league_id, team_id FROM teams WHERE league_id = 'J2018' LIMIT 1"
        )
        league_id, team_id = cur.fetchone()
    _insert_junk_picks(db, league_id, team_id, "W/R", count=1)  # 1/97 ~= 1.03% < 2%

    priors = build_slot_priors(db)

    assert priors.params["n_picks_used"] == 96  # junk pick excluded
    out = capsys.readouterr().out
    assert "dropping 1/97" in out
    assert "W/R" in out


def test_build_slot_priors_fails_loud_on_excessive_junk_positions(db):
    """>=2% junk positions: refuse to build rather than silently dropping a
    meaningful chunk of the history."""
    all_slots_qb = {slot: "QB" for slot in range(1, 13)}
    for season in range(2018, 2026):
        _seed_league(db, f"J{season}", season, all_slots_qb)
    with db.cursor() as cur:
        cur.execute(
            "SELECT league_id, team_id FROM teams WHERE league_id = 'J2018' LIMIT 1"
        )
        league_id, team_id = cur.fetchone()
    _insert_junk_picks(db, league_id, team_id, "W/R", count=2)  # 2/98 ~= 2.04% >= 2%

    with pytest.raises(ValueError, match="unexpected position"):
        build_slot_priors(db)


def test_build_slot_priors_raises_on_floored_out_slot(db):
    """When an annotation floor excludes a slot's entire draft history,
    build_slot_priors must raise ValueError naming the affected slot(s),
    not silently drop them from pos_share. Task 6 sim requires full 12x19
    coverage."""
    all_slots_qb = {slot: "QB" for slot in range(1, 13)}
    for season in range(2018, 2026):
        _seed_league(db, f"F{season}", season, all_slots_qb)

    # Set annotation floor for slot 5 at 2026 — after all draft picks (2018-2025).
    # This removes 100% of slot 5's draft history.
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.manager_slot_annotations "
            "(league_slot, human_label, from_season, note) "
            "VALUES (5, 'TestHuman', 2026, 'test seed — floors out entire history') "
            "ON CONFLICT (league_slot, from_season) DO NOTHING"
        )
    db.commit()

    with pytest.raises(ValueError, match="incomplete slot-round coverage"):
        build_slot_priors(db)
