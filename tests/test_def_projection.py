"""def_projection.py: Sleeper DEF season-projection scoring.

Verified live (scripts/verify_dst_semantics.py, docs/research/
2026-07-10-dst-semantics.md): Sleeper's season-level snapshot gives 4 real
counting stats per team (sack, int, fum_rec, blk_kick) whose weighted sum
(1,2,2,2 — identical to this league's cfg.defense.weights) reconstructs
Sleeper's own pts_std EXACTLY for 32/32 teams. The bucket fields
pts_allow_0 / yds_allow_0_100 are always 1.0 for every team (zero variance)
and contribute nothing to that reconstruction — verified-junk placeholders,
not real per-team tier data. A handful of rare TD-adjacent keys
(pass_int_td, def_fum_td, pr_td, def_kr_td) are nonzero for only 1-5/32
teams and also verified zero-weight. All of the above are ignored.

The tier-bucket mechanism (pts_allow_1_6, yds_allow_300_349, ...) is wired
in generically against cfg.defense.points_allowed_tiers/yards_allowed_tiers
for schema robustness (never fires against the current live snapshot, since
only the two verified-junk single-bucket names ever appear there)."""
import json

import pytest

from ffi.scoring.config import load_config_v1
from ffi.scoring.def_projection import def_projection_points, fit_def_uplift


@pytest.fixture(scope="module")
def cfg():
    return load_config_v1()


def test_def_projection_maps_buckets_to_league_tiers(cfg):
    stats = {
        "sack": 40.0,
        "int": 12.0,
        "fum_rec": 8.0,
        "blk_kick": 1.0,
        "pts_allow_0": 1.0,
        "pts_allow_1_6": 2.0,
        "pts_allow_7_13": 5.0,
        "pts_allow_14_20": 5.0,
        "pts_allow_21_27": 3.0,
        "pts_allow_28_34": 1.0,
        "yds_allow_300_349": 6.0,
        "gp": 17.0,
        "adp_2qb": 999.0,
    }
    pts, comps = def_projection_points(stats, cfg, uplift_per_week=3.0)
    assert pts > 0
    assert comps["uplift"] == pytest.approx(3.0 * 17)
    # tier component = sum(count * league tier points for the bucket's range)
    assert "pts_allow_tiers" in comps and "counting" in comps


def test_def_projection_fails_loud_on_unknown_stat_key(cfg):
    with pytest.raises(ValueError, match="unmapped DEF stat key"):
        def_projection_points({"brand_new_key": 1.0}, cfg, uplift_per_week=0.0)


def test_counting_stats_use_league_weights_exactly(cfg):
    # sack*1 + int*2 + fum_rec*2 + blk_kick*2, no bucket/uplift noise.
    pts, comps = def_projection_points(
        {"sack": 34.0, "int": 8.0, "fum_rec": 7.0, "blk_kick": 1.0},
        cfg,
        uplift_per_week=0.0,
    )
    assert comps["counting"] == pytest.approx(34 + 8 * 2 + 7 * 2 + 1 * 2)  # == 66
    assert pts == pytest.approx(66.0)


def test_verified_junk_bucket_keys_score_zero(cfg):
    # pts_allow_0 / yds_allow_0_100: always 1.0 live, proven zero-weight by
    # exact pts_std reconstruction (32/32 teams) — must not silently inject
    # tier points just because the key name looks like a bucket.
    pts, comps = def_projection_points(
        {"sack": 10.0, "pts_allow_0": 1.0, "yds_allow_0_100": 1.0},
        cfg,
        uplift_per_week=0.0,
    )
    assert pts == pytest.approx(10.0)
    assert comps["pts_allow_tiers"] == 0
    assert comps["yds_allow_tiers"] == 0


def test_rare_td_adjacent_keys_ignored(cfg):
    # pass_int_td/def_fum_td/pr_td/def_kr_td: nonzero for only 1-5/32 teams
    # live, verified zero-weight against Sleeper's own pts_std.
    stats = {
        "sack": 10.0,
        "pass_int_td": 2.0,
        "def_fum_td": 1.0,
        "pr_td": 1.0,
        "def_kr_td": 1.0,
        "gp": 17.0,
        "pts_ppr": 999.0,
        "pts_std": 888.0,
        "pts_half_ppr": 777.0,
        "adp_dynasty_2qb": 55.0,
    }
    pts, _ = def_projection_points(stats, cfg, uplift_per_week=0.0)
    assert pts == pytest.approx(10.0)


def test_uplift_scales_with_games(cfg):
    _, comps = def_projection_points({}, cfg, uplift_per_week=2.5, games=10.0)
    assert comps["uplift"] == pytest.approx(25.0)


def test_missing_stat_key_ok_zero_points(cfg):
    pts, comps = def_projection_points({}, cfg, uplift_per_week=0.0)
    assert pts == 0
    assert comps["counting"] == 0


def _yahoo_def_week(
    week,
    sack,
    intr,
    fum_rec,
    blk_kick,
    td,
    safe,
    tfl,
    three_outs,
    dwn4,
    xpr,
    pts_allow,
    yds_allow,
    pts_allow_bucket,
    yds_allow_bucket,
):
    pts_buckets = {
        "Pts Allow 0": 0.0,
        "Pts Allow 1-6": 0.0,
        "Pts Allow 7-13": 0.0,
        "Pts Allow 14-20": 0.0,
        "Pts Allow 21-27": 0.0,
        "Pts Allow 28-34": 0.0,
        "Pts Allow 35+": 0.0,
    }
    pts_buckets[pts_allow_bucket] = 1.0
    yds_buckets = {
        "Yds Allow Neg": 0.0,
        "Yds Allow 0-99": 0.0,
        "Yds Allow 100-199": 0.0,
        "Yds Allow 200-299": 0.0,
        "Yds Allow 300-399": 0.0,
        "Yds Allow 400-499": 0.0,
        "Yds Allow 500+": 0.0,
    }
    yds_buckets[yds_allow_bucket] = 1.0
    return {
        "name": "TestTeam",
        "player_id": 900001,
        "position_type": "DT",
        "total_points": "0.00",
        "TD": td,
        "Int": intr,
        "TFL": tfl,
        "XPR": xpr,
        "Sack": sack,
        "Safe": safe,
        "Fum Rec": fum_rec,
        "Blk Kick": blk_kick,
        "Pts Allow": pts_allow,
        "3 and Outs": three_outs,
        "4 Dwn Stops": dwn4,
        "Def Yds Allow": yds_allow,
        **pts_buckets,
        **yds_buckets,
    }


def test_fit_def_uplift_averages_the_non_sleeper_residual(db):
    cfg = load_config_v1()
    # week 1: TD=1(*6=6) + Safe=0 + 4Dwn=1(*2=2) + TFL=5(*1=5) + 3out=2(*1=2)
    #   + XPR=0 => weights=15; pts_allow=10 (tier 7-13 => +4), yds_allow=250
    #   (tier 200-299 => +4) => def_tiers=8. residual = 23.
    # week 2: TD=0 + Safe=1(*2=2) + 4Dwn=0 + TFL=3(*1=3) + 3out=1(*1=1)
    #   + XPR=1(*2=2) => weights=8; pts_allow=30 (tier 28-34 => -1), yds_allow=450
    #   (tier 400-499 => -4) => def_tiers=-5. residual = 3.
    # sack/int/fum_rec/blk_kick are zeroed before scoring regardless of the
    # (irrelevant, nonzero) values fed in below — proving the zero-out
    # actually fires, not just coincidentally scoring zero already.
    rows = [
        (
            1,
            _yahoo_def_week(
                1,
                3.0,
                1.0,
                1.0,
                0.0,
                1.0,
                0.0,
                5.0,
                2.0,
                1.0,
                0.0,
                10.0,
                250.0,
                "Pts Allow 7-13",
                "Yds Allow 200-299",
            ),
        ),
        (
            2,
            _yahoo_def_week(
                2,
                5.0,
                2.0,
                0.0,
                1.0,
                0.0,
                1.0,
                3.0,
                1.0,
                0.0,
                1.0,
                30.0,
                450.0,
                "Pts Allow 28-34",
                "Yds Allow 400-499",
            ),
        ),
    ]
    with db.cursor() as cur:
        for week, stats in rows:
            cur.execute(
                """INSERT INTO raw.yahoo_player_week
                   (league_key, season, week, yahoo_player_id, total_points, stats)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                ("test.l.1", 2099, week, "900001", "0.00", json.dumps(stats)),
            )
    db.commit()
    uplift = fit_def_uplift(db, cfg, season=2099)
    assert uplift == pytest.approx((23.0 + 3.0) / 2)
