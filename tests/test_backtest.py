import json

import pytest

from ffi.sim.backtest import (
    build_synthetic_curve,
    composite_and_band,
    enforce_match_gate,
    evaluate_gate,
    load_xwalk_lookup,
    match_row,
    normalize_name,
    season_data_vintage,
    statline_from_archive,
    synthetic_proj_points,
    upsert_season_pool,
    validate_pool_adequacy,
)
from ffi.sim.pool import PoolPlayer


def _insert_xwalk(db, name, position, gsis_id=None, fp_id=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, position, gsis_id, fantasypros_id)"
            " VALUES (%s,%s,%s,%s) RETURNING xwalk_id",
            (name, position, gsis_id, fp_id),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Name matching (fp_id primary, name fallback, override, gate)
# ---------------------------------------------------------------------------


def test_normalize_name_strips_suffix_and_punctuation():
    assert normalize_name("Patrick Mahomes II") == "patrick mahomes"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("A.J. Brown Jr.") == "aj brown"


def test_match_row_resolves_via_fp_id(db):
    _insert_xwalk(db, "Justin Jefferson", "WR", gsis_id="00-0036322", fp_id="19236")
    db.commit()
    by_fpid, by_namepos = load_xwalk_lookup(db)
    row = {"name": "Some Other Spelling", "position": "WR", "fp_id": "19236"}
    result = match_row(row, by_fpid, by_namepos, overrides={})
    assert result.gsis_id == "00-0036322"
    assert result.method == "fp_id"


def test_match_row_falls_back_to_name_when_fp_id_absent(db):
    _insert_xwalk(db, "Christian McCaffrey", "RB", gsis_id="00-0033280", fp_id="16393")
    db.commit()
    by_fpid, by_namepos = load_xwalk_lookup(db)
    row = {"name": "Christian McCaffrey", "position": "RB", "fp_id": "99999999"}
    result = match_row(row, by_fpid, by_namepos, overrides={})
    assert result.gsis_id == "00-0033280"
    assert result.method == "name"


def test_match_row_maps_k_to_pk_for_name_fallback(db):
    _insert_xwalk(db, "Chase McLaughlin", "PK", gsis_id="00-0035358", fp_id="19058")
    db.commit()
    by_fpid, by_namepos = load_xwalk_lookup(db)
    row = {"name": "Chase McLaughlin", "position": "K", "fp_id": "does-not-exist"}
    result = match_row(row, by_fpid, by_namepos, overrides={})
    assert result.gsis_id == "00-0035358"
    assert result.method == "name"


def test_match_row_honors_override_file(db):
    # No xwalk row at all -- only the override can resolve this player.
    row = {"name": "Stubborn Guy", "position": "WR", "fp_id": "no-match"}
    overrides = {"stubborn guy|WR": "00-0099999"}
    result = match_row(row, {}, {}, overrides)
    assert result.gsis_id == "00-0099999"
    assert result.method == "override"


def test_match_row_unmatched_when_nothing_resolves(db):
    row = {"name": "Nobody Special", "position": "WR", "fp_id": "no-match"}
    result = match_row(row, {}, {}, overrides={})
    assert result.gsis_id is None
    assert result.method == "unmatched"


def test_enforce_match_gate_passes_at_or_above_threshold():
    rows = [{"name": f"Player{i}"} for i in range(100)]
    matches = [
        type("M", (), {"gsis_id": ("g%d" % i) if i < 85 else None})()
        for i in range(100)
    ]
    enforce_match_gate(rows, matches, season=2023)  # 85/100 == 85% -- must not raise


def test_enforce_match_gate_exits_loud_below_threshold():
    rows = [{"name": f"Player{i}"} for i in range(100)]
    matches = [
        type("M", (), {"gsis_id": ("g%d" % i) if i < 84 else None})()
        for i in range(100)
    ]
    with pytest.raises(SystemExit, match="84/100"):
        enforce_match_gate(rows, matches, season=2023)


# ---------------------------------------------------------------------------
# Degraded synthetic curve
# ---------------------------------------------------------------------------


def _pp(position, proj_points):
    return PoolPlayer(
        ref=f"r{proj_points}",
        name="X",
        position=position,
        proj_points=proj_points,
        vorp=0.0,
        tier=1,
        adp=None,
        gsis_id=None,
    )


def test_synthetic_curve_monotone_in_rank():
    current_pool = [_pp("RB", pts) for pts in (300.0, 150.0, 250.0, 50.0, 200.0)]
    curve = build_synthetic_curve(current_pool, "RB")
    assert curve == sorted(curve, reverse=True)
    values = [synthetic_proj_points(curve, i) for i in range(10)]
    assert all(values[i] >= values[i + 1] for i in range(len(values) - 1))


def test_synthetic_curve_clamps_beyond_current_pool_length():
    current_pool = [_pp("TE", 100.0), _pp("TE", 80.0)]
    curve = build_synthetic_curve(current_pool, "TE")
    assert synthetic_proj_points(curve, 0) == 100.0
    assert synthetic_proj_points(curve, 1) == 80.0
    assert (
        synthetic_proj_points(curve, 50) == 80.0
    )  # clamped to last (replacement-level) value


def test_synthetic_curve_fails_loud_when_position_absent():
    with pytest.raises(ValueError, match="RB"):
        build_synthetic_curve([_pp("TE", 100.0)], "RB")


# ---------------------------------------------------------------------------
# Archive stat-line mapping (Task 10 carry-forward fact #1)
# ---------------------------------------------------------------------------


def test_statline_from_archive_maps_known_fields():
    stats = {
        "PASSING_ATT": 600.0,
        "PASSING_CMP": 400.0,
        "PASSING_YDS": 4800.0,
        "PASSING_TDS": 37.0,
        "PASSING_INTS": 11.0,
        "MISC_FL": 2.0,
        "MISC_FPTS": 999.0,  # ignored -- never trusted as points
    }
    line = statline_from_archive(stats, "QB")
    assert line.pass_completions == 400.0
    assert line.pass_incompletions == 200.0
    assert line.pass_yards == 4800.0
    assert line.pass_tds == 37.0
    assert line.interceptions == 11.0
    assert line.fumbles_lost == 2.0
    assert line.rush_yards is None  # absent from this stat line -> None, not 0


def test_statline_from_archive_fails_loud_on_unknown_field():
    with pytest.raises(ValueError, match="unknown archive stat field"):
        statline_from_archive({"SOME_WEIRD_STAT": 1.0}, "QB")


# ---------------------------------------------------------------------------
# Composite math
# ---------------------------------------------------------------------------


def test_composite_and_band_stubbed_two_cell():
    composite, band = composite_and_band([0.5, 0.52])
    assert composite == pytest.approx(0.51)
    assert band > 0


def test_composite_and_band_requires_at_least_two_cells():
    with pytest.raises(ValueError):
        composite_and_band([0.5])


# ---------------------------------------------------------------------------
# Gate exit codes
# ---------------------------------------------------------------------------


def test_evaluate_gate_passes_silently():
    evaluate_gate(composite=0.50, reference=(0.49, 0.02))  # no exception


def test_evaluate_gate_fails_below_threshold():
    with pytest.raises(SystemExit, match="FAILED"):
        evaluate_gate(composite=0.40, reference=(0.49, 0.02))


def test_evaluate_gate_no_active_reference():
    with pytest.raises(SystemExit, match="--reference"):
        evaluate_gate(composite=0.50, reference=None)


# ---------------------------------------------------------------------------
# Pool adequacy
# ---------------------------------------------------------------------------


def test_validate_pool_adequacy_passes_with_enough_players():
    rows_by_position = {
        "QB": [{}] * 30,
        "RB": [{}] * 40,
        "WR": [{}] * 50,
        "TE": [{}] * 20,
        "K": [{}] * 15,
        "DEF": [{}] * 32,
    }
    validate_pool_adequacy(rows_by_position)  # no exception


def test_validate_pool_adequacy_fails_loud_when_thin():
    rows_by_position = {
        "QB": [{}] * 10,
        "RB": [{}] * 40,
        "WR": [{}] * 50,
        "TE": [{}] * 20,
        "K": [{}] * 15,
        "DEF": [{}] * 32,
    }
    with pytest.raises(ValueError, match="QB"):
        validate_pool_adequacy(rows_by_position)


# ---------------------------------------------------------------------------
# Season data vintage (per-position degraded flag + fraction)
# ---------------------------------------------------------------------------


def _pool_row(ref, name, position, degraded, adp=10.0):
    return {
        "ref": ref,
        "name": name,
        "position": position,
        "proj_points": 100.0,
        "vorp": 10.0,
        "tier": 1,
        "adp": adp,
        "degraded": degraded,
    }


def test_season_data_vintage_reports_degraded_fraction_by_position(db):
    rows = [
        _pool_row("qb1", "QB One", "QB", degraded=False),
        _pool_row("qb2", "QB Two", "QB", degraded=False),
        _pool_row("rb1", "RB One", "RB", degraded=True),
        _pool_row("rb2", "RB Two", "RB", degraded=False),
    ]
    upsert_season_pool(db, 2020, rows)
    vintage = season_data_vintage(db, 2020)
    assert vintage["degraded_fraction_by_pos"] == {"QB": 0.0, "RB": 0.5}
    assert vintage["degraded_by_position"] == {"QB": False, "RB": True}
