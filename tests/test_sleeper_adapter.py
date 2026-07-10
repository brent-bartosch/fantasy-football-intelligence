import pytest

from ffi.ingest.base import IngestError
from ffi.scoring.sleeper_adapter import stat_line_from_sleeper

QB_REC = {
    "player_id": "4943",
    "player": {"position": "QB"},
    "stats": {
        "gp": 17.0,
        "pass_att": 550.0,
        "pass_cmp": 350.0,
        "pass_inc": 200.0,
        "pass_yd": 4100.0,
        "pass_td": 28.0,
        "pass_int": 11.0,
        "pass_int_td": 1.0,
        "pass_fd": 190.0,
        "pass_2pt": 1.0,
        "rush_att": 45.0,
        "rush_yd": 220.0,
        "rush_td": 2.0,
        "rush_fd": 14.0,
        "rush_2pt": 0.0,
        "fum": 6.0,
        "fum_lost": 3.0,
        "cmp_pct": 63.6,
        "pts_ppr": 400.0,
        "pts_std": 400.0,
        "pts_half_ppr": 400.0,
        "adp_dd_ppr": 30.0,
        "pos_adp_dd_ppr": 4.0,
        "pass_sack": 30.0,
        "pass_cmp_40p": 8.0,
        "rush_40p": 0.0,
        "def_fum_td": 0.0,
    },
}


def test_qb_mapping():
    line = stat_line_from_sleeper(QB_REC)
    assert line.pass_completions == 350.0
    assert line.pass_incompletions == 200.0
    assert line.pick_sixes == 1.0  # pass_int_td
    assert line.interceptions == 11.0
    assert line.two_point_conversions == 1.0  # pass_2pt + rush_2pt + rec_2pt
    # rush_fd/rec_fd are deliberately NOT mapped into the StatLine (2026-07-09):
    # Sleeper's native FD is rejected as a scoring input — see sleeper_adapter's
    # _IGNORED_EXACT comment and docs/research/2026-07-09-fd-imputation-divergence.md.
    # Imputed FD (ffi.scoring.fd_impute) is injected downstream instead.
    assert line.rush_first_downs is None
    assert line.receptions is None  # absent for this QB record


def test_unknown_stat_key_fails_loud():
    rec = {**QB_REC, "stats": {**QB_REC["stats"], "brand_new_stat": 1.0}}
    with pytest.raises(IngestError, match="unmapped"):
        stat_line_from_sleeper(rec)


def test_missing_stats_fails_loud():
    with pytest.raises(IngestError):
        stat_line_from_sleeper({"player_id": "1", "player": {"position": "QB"}})


def test_kicker_mapping_uses_real_live_key_names():
    # Real live season-level vocabulary (2026, snapshot_id=3) only projects
    # 40-49/50+ FG buckets and PATs — 0-39 buckets are simply absent live,
    # confirmed via psql against raw.sleeper_projections.
    rec = {
        "player_id": "1023",
        "player": {"position": "K"},
        "stats": {
            "gp": 17.0,
            "fgm_40_49": 3.0,
            "fgm_50p": 1.0,
            "fgm_yds": 410.0,
            "fgmiss_40_49": 1.0,
            "fgmiss_50p": 1.0,
            "xpm": 35.0,
            "xpmiss": 1.0,
            "pts_ppr": 120.0,
            "pts_std": 120.0,
            "pts_half_ppr": 120.0,
            "adp_ppr": 150.0,
        },
    }
    line = stat_line_from_sleeper(rec)
    assert line.fg_40_49 == 3.0
    assert line.fg_50_plus == 1.0
    assert line.pat_made == 35.0
    assert line.pat_missed == 1.0
    # fgmiss_40_49 / fgmiss_50p: observed live but StatLine/league config only
    # score misses through 30-39 — deliberately ignored, not silently dropped.
    assert line.fg_miss_0_19 is None


def test_return_td_mapping_pr_td_and_def_kr_td():
    # pr_td (individual punt-return TD) and def_kr_td (kick-return TD) both
    # appear live on WR/RB *and* DEF-position records (verified via psql) and
    # map unambiguously to the existing return_tds field/weight — summed like
    # two-point conversions.
    rec = {
        "player_id": "555",
        "player": {"position": "WR"},
        "stats": {"gp": 17.0, "rec": 60.0, "pr_td": 1.0, "def_kr_td": 1.0},
    }
    line = stat_line_from_sleeper(rec)
    assert line.return_tds == 2.0


def test_def_position_ignores_deferred_dst_stats():
    # Team-DST stats (sack/int/fum_rec/blk_kick/pts_allow_0/yds_allow_0_100)
    # are real, observed-live, DEF-only keys (verified via psql) but full DST
    # scoring semantics are out of scope for this task — deliberately ignored
    # rather than guessed at, per fail-loud (a wrong guess would silently
    # corrupt DST points; an explicit ignore does not).
    rec = {
        "player_id": "TB",
        "player": {"position": "DEF"},
        "stats": {
            "gp": 17.0,
            "sack": 40.0,
            "int": 12.0,
            "fum_rec": 8.0,
            "blk_kick": 1.0,
            "def_fum_td": 1.0,
            "pts_allow_0": 2.0,
            "yds_allow_0_100": 3.0,
        },
    }
    line = stat_line_from_sleeper(rec)
    assert line.sacks is None
    assert line.def_interceptions is None
